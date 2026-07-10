import asyncio
import html
import logging
import re
import time
from collections import defaultdict
from datetime import datetime, time as dtime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from telegram import (
    BotCommand,
    BotCommandScopeChat,
    BotCommandScopeDefault,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputProfilePhotoStatic,
    Update,
)
from telegram.constants import ChatAction, ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from telegram.error import BadRequest, Forbidden

import pymupdf
from qdrant_client.http.exceptions import UnexpectedResponse

from src import acl, invites, vector_store
from src.agent import AgentResponse, Source, run as agent_run
from src.analytics import get_recent_questions, get_stats, log_escalation, log_query
from src.config import CONFIG, TELEGRAM_BOT_TOKEN
from src.embeddings import embed_documents
from src.contextualizer import contextualize_chunks
from src.pdf_loader import ScannedPDFError, process_single_pdf_with_doc
from src.vector_store import delete_by_filename, upsert_chunks

logger = logging.getLogger(__name__)

USE_CONTEXTUAL = CONFIG.get("contextual_embeddings", {}).get("enabled", False)
MAX_HISTORY = 5
PDF_DIR = Path(__file__).resolve().parent.parent / "data" / "pdfs"

# Rate limiting
_RATE_WINDOW = CONFIG["telegram"].get("rate_limit_window", 60)  # seconds
_RATE_MAX = CONFIG["telegram"].get("rate_limit_max", 10)  # messages per window
_MAX_MESSAGE_LENGTH = CONFIG["telegram"].get("max_message_length", 4000)  # chars
_MAX_PDF_SIZE_MB = CONFIG["telegram"].get("max_pdf_size_mb", 20)
_PAGE_WARN_THRESHOLD = 35
_MAX_CHUNKS = 600
_INACTIVE_TTL_S = CONFIG["telegram"].get("inactive_ttl_minutes", 60) * 60  # seconds

# Confidence sentinel emitted by the LLM at end of every response.
# Stripped before sending to user; system appends the matching disclaimer below.
# [CONF:LOW] and [CONF:NONE] both drive escalation notifications to admins.
_CONF_PATTERN = re.compile(r"\[CONF:(HIGH|MED|LOW|NONE|NA)\]\s*$", re.IGNORECASE)
_ESCALATE_LEVELS = frozenset({"LOW", "NONE"})

# Progress statuses shown (and edited in place) while the agent works.
# Client constraint: wording must never reveal internet/web usage —
# web_search maps to a generic "checking additional sources" phrase.
_STATUS_INITIAL = "⏳ Analiziram vaše pitanje…"
_STATUS_BY_TOOL = [
    ("search_documents", "📚 Pretražujem dokumentaciju…"),
    ("web_search", "🔎 Proveravam dodatne izvore…"),
    ("code_execution", "🧮 Obrađujem podatke…"),
]
_STATUS_DEFAULT = "✍️ Pripremam odgovor…"


def _status_for_tools(tools: list[str]) -> str:
    for tool, status in _STATUS_BY_TOOL:
        if tool in tools:
            return status
    return _STATUS_DEFAULT

_CONTACT_BLOCK = (
    "\n\nZakažite konsultacije:\n"
    "Kalendar i kontakt:\n"
    "0112392909\n"
    "0112360575"
)

_DISCLAIMERS = {
    "HIGH": (
        "\n\nNapomena: Ovaj odgovor je informativnog karaktera. "
        "Za potvrdu i primenu na vašu konkretnu situaciju, možete kontaktirati Mari-Mar doo."
    ) + _CONTACT_BLOCK,
    "MED": (
        "\n\nNapomena: Ovaj odgovor je informativnog karaktera i može zavisiti od specifičnosti "
        "vaše situacije. Za tačan i potpun savet, preporučujemo da kontaktirate stručni tim Mari-Mar doo."
    ) + _CONTACT_BLOCK,
    "LOW": (
        "\n\nNapomena: Informacije u ovom odgovoru služe isključivo u opšte informativne svrhe "
        "i ne predstavljaju zvaničan poreski ili knjigovodstveni savet. Za donošenje odluka, "
        "neophodna je konsultacija sa stručnim timom Mari-Mar doo."
    ) + _CONTACT_BLOCK,
    "NONE": (
        "\n\nTrenutno nemam dovoljno pouzdanih informacija da bih dao tačan odgovor na vaše pitanje. "
        "Predlažem da se obratite direktno stručnom timu Mari-Mar doo, kako biste dobili precizan i pouzdan savet."
    ) + _CONTACT_BLOCK,
    "NA": "",  # out-of-scope: no disclaimer, no contact block
}


def _apply_disclaimer(text: str) -> tuple[str, str]:
    """Strip trailing [CONF:*] sentinel and return (clean_text_with_disclaimer, level).

    Falls back to LOW if the LLM omits the sentinel — safer than HIGH for legal posture.
    """
    match = _CONF_PATTERN.search(text)
    if match:
        level = match.group(1).upper()
        clean = text[: match.start()].rstrip()
    else:
        level = "LOW"
        clean = text.rstrip()
        logger.warning("Response missing [CONF:*] sentinel — defaulting to LOW")
    return clean + _DISCLAIMERS[level], level


def _strip_sentinel(text: str) -> str:
    """Remove [CONF:*] from text without adding a disclaimer (for chat history storage)."""
    return _CONF_PATTERN.sub("", text).rstrip()

_USER_COMMANDS = [
    BotCommand("start", "Pozdravna poruka i vaš ID"),
    BotCommand("help", "Prikaži listu komandi"),
    BotCommand("clear", "Obriši istoriju razgovora"),
]

_ADMIN_COMMANDS = _USER_COMMANDS + [
    BotCommand("dokumenti", "Prikaži dokumente u bazi znanja"),
    BotCommand("adduser", "Dodaj korisnika"),
    BotCommand("removeuser", "Ukloni korisnika"),
    BotCommand("users", "Prikaži listu korisnika"),
    BotCommand("addadmin", "Dodaj admina"),
    BotCommand("removeadmin", "Ukloni admina"),
    BotCommand("stats", "Statistika korišćenja"),
    BotCommand("pitanja", "Lista pitanja iz prethodnog perioda"),
    BotCommand("broadcast", "Pošalji obaveštenje svima"),
    BotCommand("invite", "Kreiraj pozivni link za klijenta"),
]

_chat_history: dict[int, list[dict]] = defaultdict(list)
_rate_timestamps: dict[int, list[float]] = defaultdict(list)
_last_activity: dict[int, float] = {}

# Registration form state (in-memory, lost on restart — acceptable)
_REG_ASK_NAME = "ask_name"
_REG_ASK_COMPANY = "ask_company"
_REG_STATE_TTL = 1800  # 30 min
_registration_state: dict[int, dict] = {}


def _sweep_inactive() -> None:
    """Remove history, rate-limit, and stale registration entries for idle chats."""
    now = time.monotonic()
    stale = [cid for cid, ts in _last_activity.items() if now - ts > _INACTIVE_TTL_S]
    for cid in stale:
        _chat_history.pop(cid, None)
        _rate_timestamps.pop(cid, None)
        _last_activity.pop(cid, None)
    if stale:
        logger.info("Swept %d inactive chat(s)", len(stale))
    # Evict abandoned registration forms
    stale_reg = [
        cid for cid, s in _registration_state.items()
        if now - s.get("started_at", 0) > _REG_STATE_TTL
    ]
    for cid in stale_reg:
        _registration_state.pop(cid, None)
    if stale_reg:
        logger.info("Swept %d stale registration(s)", len(stale_reg))


async def _sweep_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    _sweep_inactive()


def _is_rate_limited(chat_id: int) -> bool:
    """Sliding window rate limiter. Returns True if user should be blocked."""
    now = time.monotonic()
    timestamps = _rate_timestamps[chat_id]
    # Prune old entries
    _rate_timestamps[chat_id] = [t for t in timestamps if now - t < _RATE_WINDOW]
    if len(_rate_timestamps[chat_id]) >= _RATE_MAX:
        return True
    _rate_timestamps[chat_id].append(now)
    return False


def _is_allowed(chat_id: int) -> bool:
    return acl.is_allowed(chat_id)


def _is_admin(chat_id: int) -> bool:
    return acl.is_admin(chat_id)


async def _notify_admins_escalation(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    user_text: str,
    bot_response: str,
) -> None:
    """Fire-and-forget: notify all admins of an escalation."""
    name = acl.get_name(chat_id) or str(chat_id)
    # Truncate bot response to keep notification readable
    response_preview = bot_response[:500] + "..." if len(bot_response) > 500 else bot_response
    text = (
        f"⚠️ Eskalacija\n"
        f"Korisnik: {name}\n"
        f"Pitanje: {user_text}\n\n"
        f"Odgovor bota:\n{response_preview}"
    )
    for admin_id in acl.get_admins():
        try:
            await context.bot.send_message(chat_id=admin_id, text=text)
        except Exception:
            logger.exception("Failed to notify admin %s of escalation", admin_id)


async def _notify_admins_invite_used(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    label: str,
    created_by: int,
) -> None:
    """Fire-and-forget: notify all admins that an invite was consumed."""
    safe_label = html.escape(label)
    text = (
        f"✅ Pozivnica iskorišćena\n"
        f"Korisnik: {safe_label}\n"
        f"Chat ID: {chat_id}\n"
        f"Pozivnicu kreirao: {_display_id(created_by)}"
    )
    for admin_id in acl.get_admins():
        try:
            await context.bot.send_message(chat_id=admin_id, text=text)
        except Exception:
            logger.exception("Failed to notify admin %s of invite use", admin_id)


def _display_id(chat_id: int) -> str:
    """Format chat ID with name if known: 'Name (ID)' or just 'ID'."""
    name = acl.get_name(chat_id)
    return f"{name} ({chat_id})" if name else str(chat_id)


async def _notify_admins_registration(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    name: str,
    company: str,
    username: str | None,
) -> None:
    """Send registration request to all admins with inline approve/deny buttons."""
    safe_name = html.escape(name)
    safe_company = html.escape(company)
    user_tag = f"@{username}" if username else "—"
    text = (
        f"🔔 Nov zahtev za pristup\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Ime: {safe_name}\n"
        f"Firma: {safe_company}\n"
        f"Telegram: {user_tag}\n"
        f"ID: {chat_id}"
    )
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Odobri", callback_data=f"reg_approve:{chat_id}"),
            InlineKeyboardButton("❌ Odbij", callback_data=f"reg_deny:{chat_id}"),
        ]
    ])
    for admin_id in acl.get_admins():
        try:
            await context.bot.send_message(
                chat_id=admin_id, text=text, reply_markup=keyboard,
            )
        except Exception:
            logger.exception("Failed to notify admin %s of registration", admin_id)


async def _handle_registration_step(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> bool:
    """Handle a message from a user mid-registration. Returns True if handled."""
    chat_id = update.effective_chat.id
    state = _registration_state.get(chat_id)
    if state is None:
        return False

    text = update.message.text.strip() if update.message.text else ""

    if state["step"] == _REG_ASK_NAME:
        if not text or len(text) > 100:
            await update.message.reply_text("Unesite vaše ime i prezime (do 100 karaktera).")
            return True
        state["name"] = text
        state["step"] = _REG_ASK_COMPANY
        await update.message.reply_text("Naziv firme?")
        return True

    if state["step"] == _REG_ASK_COMPANY:
        if not text or len(text) > 100:
            await update.message.reply_text("Unesite naziv firme (do 100 karaktera).")
            return True
        name = state["name"]
        company = text
        _registration_state.pop(chat_id, None)
        # Check for existing pending registration (rate limit)
        if invites.get_registration(chat_id):
            await update.message.reply_text(
                "Već imate aktivan zahtev za pristup. Sačekajte odobrenje administratora."
            )
            return True
        invites.add_registration(chat_id, name, company)
        await update.message.reply_text(
            "✅ Zahtev poslat.\n"
            "Administrator će vas odobriti u najkraćem roku."
        )
        username = update.effective_user.username if update.effective_user else None
        asyncio.create_task(
            _notify_admins_registration(context, chat_id, name, company, username)
        )
        return True

    return False


async def _registration_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle inline approve/deny button taps from admins."""
    query = update.callback_query
    admin_id = query.from_user.id
    if not _is_admin(admin_id):
        await query.answer("Nemate pristup.", show_alert=True)
        return

    data = query.data or ""
    if ":" not in data:
        await query.answer("Nevažeći podatak.")
        return

    action, chat_id_str = data.split(":", 1)
    try:
        target_chat_id = int(chat_id_str)
    except ValueError:
        await query.answer("Nevažeći ID.")
        return

    reg = invites.resolve_registration(target_chat_id)
    if reg is None:
        await query.answer("Zahtev je već obrađen.", show_alert=True)
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except BadRequest:
            pass
        return

    admin_name = acl.get_name(admin_id) or str(admin_id)
    now_str = datetime.now(ZoneInfo("Europe/Belgrade")).strftime("%d.%m. %H:%M")
    safe_name = html.escape(reg["name"])
    safe_company = html.escape(reg["company"])

    if action == "reg_approve":
        label = f"{reg['company']} — {reg['name']}"
        acl.add_user(target_chat_id, label)
        # Notify the user
        try:
            await context.bot.send_message(
                chat_id=target_chat_id,
                text=(
                    "✅ Pristup odobren!\n"
                    "Dobrodošli. Možete početi sa postavljanjem pitanja.\n\n"
                    "Možete me pitati, na primer:\n"
                    '• \u201eDo kada se podnosi prijava za godišnji porez na dohodak za 2025?\u201c\n'
                    '• \u201eKoja je razlika između preduzetnika i DOO u pogledu poreza?\u201c\n'
                    '• \u201eKoji je trenutni iznos minimalne zarade u Srbiji?\u201c'
                ),
            )
        except Exception:
            logger.warning("Failed to notify user %s of approval", target_chat_id)
        # Update admin message
        try:
            await query.edit_message_text(
                f"🔔 Nov zahtev za pristup\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"Ime: {safe_name}\n"
                f"Firma: {safe_company}\n"
                f"ID: {target_chat_id}\n\n"
                f"✅ Odobrio: {html.escape(admin_name)} ({now_str})",
                parse_mode=None,
            )
        except BadRequest:
            pass
        await query.answer("Korisnik odobren.")

    elif action == "reg_deny":
        # Notify the user
        try:
            await context.bot.send_message(
                chat_id=target_chat_id,
                text=(
                    "❌ Vaš zahtev je odbijen.\n"
                    "Kontaktirajte Mari-Mar za više informacija."
                ),
            )
        except Exception:
            logger.warning("Failed to notify user %s of denial", target_chat_id)
        # Update admin message
        try:
            await query.edit_message_text(
                f"🔔 Nov zahtev za pristup\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"Ime: {safe_name}\n"
                f"Firma: {safe_company}\n"
                f"ID: {target_chat_id}\n\n"
                f"❌ Odbio: {html.escape(admin_name)} ({now_str})",
                parse_mode=None,
            )
        except BadRequest:
            pass
        await query.answer("Zahtev odbijen.")

    else:
        await query.answer("Nepoznata akcija.")


def _trim_history(chat_id: int) -> None:
    history = _chat_history[chat_id]
    if len(history) > MAX_HISTORY * 2:
        _chat_history[chat_id] = history[-(MAX_HISTORY * 2):]


def _render_table(table_lines: list[str]) -> str:
    """Convert markdown pipe-table into a vertical key-value layout.

    Mobile Telegram renders <pre> blocks with tiny font and horizontal scroll,
    so we avoid monospace entirely. Instead, the first column becomes a label
    and remaining columns become "Header: Value" lines underneath.
    """
    rows: list[list[str]] = []
    for line in table_lines:
        stripped = line.strip().strip("|")
        cells = [c.strip() for c in stripped.split("|")]
        if all(re.fullmatch(r":?-+:?", c) for c in cells):
            continue
        rows.append(cells)
    if not rows:
        return ""

    n_cols = max(len(r) for r in rows)
    for r in rows:
        while len(r) < n_cols:
            r.append("")

    headers = rows[0]
    data_rows = rows[1:]

    if not data_rows:
        return " · ".join(html.escape(h) for h in headers)

    # Two-column table: render as simple "Key: Value" lines
    if n_cols == 2:
        lines = []
        for row in data_rows:
            lines.append(f"<b>{html.escape(row[0])}</b>: {html.escape(row[1])}")
        return "\n".join(lines)

    # Multi-column: first column = label, rest = "Header: Value"
    blocks = []
    for row in data_rows:
        block = f"▸ <b>{html.escape(row[0])}</b>"
        for c in range(1, n_cols):
            if row[c]:
                block += f"\n  {html.escape(headers[c])}: {html.escape(row[c])}"
        blocks.append(block)
    return "\n\n".join(blocks)


def _md_to_html(text: str) -> str:
    """Convert Markdown to Telegram-compatible HTML.

    Processes blocks (code fences, tables) first to protect them from inline
    formatting, then applies inline rules to the remaining text.
    """
    # --- Phase 1: extract fenced code blocks ---
    blocks: list[str] = []  # rendered HTML for each placeholder

    def _replace_code_block(m: re.Match) -> str:
        lang = m.group(1) or ""
        code = html.escape(m.group(2))
        if lang:
            rendered = f'<pre><code class="language-{html.escape(lang)}">{code}</code></pre>'
        else:
            rendered = f"<pre>{code}</pre>"
        idx = len(blocks)
        blocks.append(rendered)
        return f"\x00BLOCK{idx}\x00"

    text = re.sub(
        r"```(\w*)\n(.*?)```",
        _replace_code_block,
        text,
        flags=re.DOTALL,
    )

    # --- Phase 2: extract tables ---
    def _replace_table(m: re.Match) -> str:
        table_text = m.group(0)
        table_lines = [l for l in table_text.split("\n") if l.strip()]
        rendered = _render_table(table_lines)
        idx = len(blocks)
        blocks.append(rendered)
        return f"\x00BLOCK{idx}\x00"

    text = re.sub(
        r"(?:^[ \t]*\|.+\|[ \t]*\n){2,}",
        _replace_table,
        text,
        flags=re.MULTILINE,
    )

    # --- Phase 3: HTML-escape remaining text ---
    text = html.escape(text)

    # --- Phase 4: block-level formatting ---
    text = re.sub(r"^#{1,6}\s+(.+)$", r"<b>\1</b>", text, flags=re.MULTILINE)
    text = re.sub(r"^[ \t]*[-*][ \t]+", "• ", text, flags=re.MULTILINE)
    text = re.sub(r"^[ \t]{2,}[-*][ \t]+", "  ◦ ", text, flags=re.MULTILINE)
    text = re.sub(r"^-{3,}$", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\*{3,}$", "", text, flags=re.MULTILINE)

    # --- Phase 5: inline formatting (order matters) ---
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"\*(.+?)\*", r"<i>\1</i>", text)
    text = re.sub(r"~~(.+?)~~", r"<s>\1</s>", text)
    text = re.sub(r"`(.+?)`", r"<code>\1</code>", text)
    text = re.sub(
        r"\[([^\]]+)\]\(([^)]+)\)",
        lambda m: f'<a href="{m.group(2)}">{m.group(1)}</a>',
        text,
    )

    # --- Phase 6: cleanup ---
    text = text.replace("**", "")  # strip orphan bold markers
    text = re.sub(r"\n{3,}", "\n\n", text)  # collapse excessive blank lines

    # --- Phase 7: reinsert protected blocks ---
    for i, block in enumerate(blocks):
        text = text.replace(f"\x00BLOCK{i}\x00", block)

    return text.strip()


def _format_sources(sources: list[Source]) -> str:
    """Format document citations as ready-to-use HTML (appended after conversion).

    Web links are intentionally omitted — client asked to hide them from replies.
    """
    lines = [f"📄 {html.escape(s.name)}" for s in sources if s.type == "document"]
    return "\n\n" + "\n".join(lines) if lines else ""


async def _send_long(update: Update, text: str, sources_html: str = "") -> None:
    text = _md_to_html(text) + sources_html
    limit = 4096
    chunks = _split_html(text, limit)
    for chunk in chunks:
        try:
            await update.message.reply_text(chunk, parse_mode=ParseMode.HTML)
        except Exception:
            await update.message.reply_text(chunk)


def _split_html(text: str, limit: int) -> list[str]:
    """Split text into chunks that respect HTML tag boundaries."""
    if len(text) <= limit:
        return [text]

    _TAG_RE = re.compile(r"<(/?)(\w+)(?:\s[^>]*)?>")
    chunks: list[str] = []
    pos = 0

    while pos < len(text):
        if len(text) - pos <= limit:
            chunks.append(text[pos:])
            break

        # Find last whitespace before the limit to avoid mid-word splits
        split_at = text.rfind(" ", pos, pos + limit)
        if split_at <= pos:
            split_at = pos + limit

        # Walk back if we're inside an HTML tag (between < and >)
        bracket = text.rfind("<", pos, split_at)
        if bracket != -1 and ">" not in text[bracket:split_at]:
            split_at = bracket

        chunk = text[pos:split_at]

        # Find unclosed tags in this chunk and close them
        open_tags: list[str] = []
        for m in _TAG_RE.finditer(chunk):
            if m.group(1) == "/":
                if open_tags and open_tags[-1] == m.group(2):
                    open_tags.pop()
            else:
                open_tags.append(m.group(2))

        # Close open tags at end of chunk, reopen at start of next
        closing = "".join(f"</{tag}>" for tag in reversed(open_tags))
        reopening = "".join(f"<{tag}>" for tag in open_tags)

        chunks.append(chunk + closing)
        pos = split_at
        if reopening:
            # Prepend reopening tags to the remaining text
            text = text[:pos] + reopening + text[pos:]
            # Don't advance past the reopening tags — they're part of the next chunk

    return chunks


async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id

    # Deep-link invite token: /start <token>
    if context.args:
        token = context.args[0]
        if _is_allowed(chat_id):
            await update.message.reply_text(
                "Zdravo! Već imate pristup. Postavite mi pitanje."
            )
            return
        invite = invites.consume(token, chat_id)
        if invite is None:
            await update.message.reply_text(
                "Ovaj pozivni link nije validan ili je istekao.\n"
                "Kontaktirajte administratora za novi link."
            )
            return
        label = invite["label"]
        acl.add_user(chat_id, label)
        safe_label = html.escape(label)
        await update.message.reply_text(
            f"Dobrodošli, <b>{safe_label}</b>! 🎉\n\n"
            f"Vaš nalog je aktiviran. Možete odmah početi sa korišćenjem bota.\n\n"
            f"Možete me pitati, na primer:\n"
            f'• \u201eDo kada se podnosi prijava za godišnji porez na dohodak za 2025?\u201c\n'
            f'• \u201eKoja je razlika između preduzetnika i DOO u pogledu poreza?\u201c\n'
            f'• \u201eKoji je trenutni iznos minimalne zarade u Srbiji?\u201c',
            parse_mode=ParseMode.HTML,
        )
        asyncio.create_task(
            _notify_admins_invite_used(context, chat_id, label, invite["created_by"])
        )
        return

    # Normal /start — no token
    if _is_allowed(chat_id):
        await update.message.reply_text(
            "Zdravo! 👋\n\n"
            "Ja sam računovodstveni asistent kompanije Mari-Mar. "
            "Pomažem sa pitanjima iz oblasti poreza, računovodstva i poslovnih propisa.\n\n"
            "Možete me pitati, na primer:\n"
            '• \u201eDo kada se podnosi prijava za godišnji porez na dohodak za 2025?\u201c\n'
            '• \u201eKoja je razlika između preduzetnika i DOO u pogledu poreza?\u201c\n'
            '• \u201eKoji je trenutni iznos minimalne zarade u Srbiji?\u201c'
        )
        return

    # Already has a pending registration?
    if invites.get_registration(chat_id):
        await update.message.reply_text(
            "Već imate aktivan zahtev za pristup. Sačekajte odobrenje administratora."
        )
        return

    # Stranger — start registration form. Cancel any stale form state.
    _registration_state.pop(chat_id, None)
    _registration_state[chat_id] = {
        "step": _REG_ASK_NAME,
        "started_at": time.monotonic(),
    }
    await update.message.reply_text(
        "Zdravo! 👋\n\n"
        "Da biste dobili pristup, potrebna nam je registracija.\n\n"
        "Kako se zovete? (ime i prezime)"
    )


async def adduser_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    if not _is_admin(chat_id):
        await update.message.reply_text("Nemate pristup ovoj komandi.")
        return

    if not context.args:
        await update.message.reply_text("Upotreba: /adduser <ID> [ime]")
        return

    try:
        user_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("ID mora biti broj.")
        return

    name = " ".join(context.args[1:]) or None
    result = acl.add_user(user_id, name)
    display = _display_id(user_id)
    if result == "added":
        await update.message.reply_text(f"✓ Korisnik {display} je dodat.")
    elif result == "updated":
        await update.message.reply_text(f"Korisnik {user_id} je već na listi. Ime ažurirano na '{name}'.")
    else:
        await update.message.reply_text(f"Korisnik {display} je već na listi.")


async def removeuser_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    if not _is_admin(chat_id):
        await update.message.reply_text("Nemate pristup ovoj komandi.")
        return

    if not context.args:
        await update.message.reply_text("Upotreba: /removeuser <ID>")
        return

    try:
        user_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("ID mora biti broj.")
        return

    display = _display_id(user_id)
    if acl.remove_user(user_id):
        await update.message.reply_text(f"✓ Korisnik {display} je uklonjen.")
    else:
        await update.message.reply_text(f"Korisnik {user_id} nije na listi.")


async def users_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    if not _is_admin(chat_id):
        await update.message.reply_text("Nemate pristup ovoj komandi.")
        return

    users = acl.get_users()
    if not users:
        await update.message.reply_text("Lista korisnika je prazna (svi imaju pristup).")
        return

    lines = [f"Dozvoljeni korisnici ({len(users)}):"]
    for uid in users:
        lines.append(f"• {_display_id(uid)}")
    await update.message.reply_text("\n".join(lines))


async def addadmin_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    if not _is_admin(chat_id):
        await update.message.reply_text("Nemate pristup ovoj komandi.")
        return

    if not context.args:
        await update.message.reply_text("Upotreba: /addadmin <ID> [ime]")
        return

    try:
        admin_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("ID mora biti broj.")
        return

    name = " ".join(context.args[1:]) or None
    result = acl.add_admin(admin_id, name)
    display = _display_id(admin_id)
    if result in ("added", "updated"):
        try:
            await context.bot.set_my_commands(
                _ADMIN_COMMANDS, scope=BotCommandScopeChat(admin_id)
            )
        except Exception:
            logger.warning("Failed to set commands for admin %s", admin_id)
        if result == "added":
            await update.message.reply_text(f"✓ Admin {display} je dodat.")
        else:
            await update.message.reply_text(f"Admin {admin_id} je već na listi. Ime ažurirano na '{name}'.")
    else:
        await update.message.reply_text(f"Admin {display} je već na listi.")


async def removeadmin_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    if not _is_admin(chat_id):
        await update.message.reply_text("Nemate pristup ovoj komandi.")
        return

    if not context.args:
        await update.message.reply_text("Upotreba: /removeadmin <ID>")
        return

    try:
        admin_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("ID mora biti broj.")
        return

    if admin_id == chat_id:
        await update.message.reply_text("Ne možete ukloniti sami sebe.")
        return

    display = _display_id(admin_id)
    if acl.remove_admin(admin_id):
        try:
            await context.bot.delete_my_commands(scope=BotCommandScopeChat(admin_id))
        except Exception:
            logger.warning("Failed to delete commands for removed admin %s", admin_id)
        await update.message.reply_text(f"✓ Admin {display} je uklonjen.")
    else:
        await update.message.reply_text(f"Admin {admin_id} nije na listi.")


async def invite_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin-only: generate a one-time invite link for a client."""
    chat_id = update.effective_chat.id
    if not _is_admin(chat_id):
        await update.message.reply_text("Nemate pristup ovoj komandi.")
        return

    if not context.args:
        await update.message.reply_text(
            "Upotreba: /invite Ime Prezime, Naziv firme\n"
            "Primer: /invite Marko Marković, ACME d.o.o."
        )
        return

    label = " ".join(context.args)
    token = invites.create(label=label, created_by=chat_id)
    bot_username = context.bot.username
    link = f"https://t.me/{bot_username}?start={token}"
    safe_label = html.escape(label)

    await update.message.reply_text(
        f"Pozivnica za <b>{safe_label}</b>:\n\n"
        f"<code>{link}</code>\n\n"
        f"Link važi 7 dana. Klijent samo klikne link i automatski dobija pristup.",
        parse_mode=ParseMode.HTML,
    )


async def help_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id

    lines = [
        "<b>Dostupne komande:</b>\n",
        "/start — Pozdravna poruka i vaš korisnički ID",
        "/clear — Obriši istoriju razgovora",
        "/help — Prikaži listu komandi",
    ]

    if _is_admin(chat_id):
        lines.append("\n<b>Admin komande:</b>\n")
        lines.append("/dokumenti — Prikaži dokumente u bazi znanja")
        lines.append("/adduser &lt;ID&gt; [ime] — Dodaj korisnika")
        lines.append("/removeuser &lt;ID&gt; — Ukloni korisnika")
        lines.append("/users — Prikaži listu korisnika")
        lines.append("/addadmin &lt;ID&gt; [ime] — Dodaj admina")
        lines.append("/removeadmin &lt;ID&gt; — Ukloni admina")
        lines.append("/stats [dana] — Statistika (podrazumevano 7 dana)")
        lines.append("/pitanja [dana] — Lista pitanja (podrazumevano 7 dana, max 90)")
        lines.append("/broadcast &lt;poruka&gt; — Pošalji obaveštenje svim korisnicima")
        lines.append("/invite &lt;ime, firma&gt; — Kreiraj pozivni link za klijenta")
        lines.append("\n📄 Pošaljite PDF fajl da biste ga dodali u bazu znanja.")

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def dokumenti_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    chat_id = update.effective_chat.id
    if not _is_admin(chat_id):
        await update.message.reply_text("Nemate pristup ovoj komandi.")
        return

    docs = await vector_store.list_documents()
    if not docs:
        await update.message.reply_text("Baza znanja je prazna.")
        return

    lines = ["<b>Dokumenti u bazi znanja:</b>\n"]
    for fn in sorted(docs):
        if _is_admin(chat_id):
            lines.append(f"• {html.escape(fn)}  ({docs[fn]} delova)")
        else:
            lines.append(f"• {html.escape(fn)}")

    text = "\n".join(lines)
    for chunk in _split_html(text, 4096):
        await update.message.reply_text(chunk, parse_mode="HTML")


async def broadcast_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    chat_id = update.effective_chat.id
    if not _is_admin(chat_id):
        await update.message.reply_text("Samo admini mogu koristiti ovu komandu.")
        return

    if not context.args:
        await update.message.reply_text("Upotreba: /broadcast <poruka>")
        return

    message = "📢 Obaveštenje:\n\n" + " ".join(context.args)

    if len(message) > 4096:
        await update.message.reply_text(
            f"Poruka je predugačka ({len(message)} karaktera). "
            "Telegram dozvoljava max 4096. Skratite tekst."
        )
        return

    logger.info("Broadcast from admin %s (%d chars)", chat_id, len(message))
    recipients = set(acl.get_users()) | set(acl.get_admins())
    if not recipients:
        await update.message.reply_text("Lista korisnika je prazna.")
        return

    await update.message.reply_text(
        f"⏳ Slanje obaveštenja ({len(recipients)} korisnika)..."
    )

    async def _do_broadcast() -> None:
        sent = failed = blocked = 0
        for uid in recipients:
            try:
                await context.bot.send_message(
                    chat_id=uid, text=message, parse_mode="HTML"
                )
                sent += 1
            except BadRequest:
                try:
                    await context.bot.send_message(chat_id=uid, text=message)
                    sent += 1
                except Exception:
                    failed += 1
            except Forbidden:
                blocked += 1
            except Exception:
                failed += 1
            await asyncio.sleep(0.05)

        logger.info(
            "Broadcast from %s result: sent=%d, failed=%d, blocked=%d",
            chat_id, sent, failed, blocked,
        )
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"✓ Poslato: {sent}, neuspešno: {failed}, blokirano: {blocked}",
            )
        except Exception:
            logger.exception("Failed to send broadcast summary to admin %s", chat_id)

    asyncio.create_task(_do_broadcast())


async def clear_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    if not _is_allowed(chat_id):
        await update.message.reply_text("Nemate pristup ovom botu.")
        return

    _chat_history[chat_id].clear()
    await update.message.reply_text("Istorija razgovora je obrisana.")


async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id

    # ACL check first — already-approved users always pass through,
    # even if they have a stale pending registration.
    if _is_allowed(chat_id):
        pass  # fall through to normal message handling below
    elif await _handle_registration_step(update, context):
        # Registration form in progress — intercept
        return
    elif invites.get_registration(chat_id):
        # Pending registration — user is waiting for approval
        await update.message.reply_text(
            "Vaš zahtev za pristup je na čekanju. Sačekajte odobrenje administratora."
        )
        return
    else:
        await update.message.reply_text("Nemate pristup ovom botu.")
        return

    _last_activity[chat_id] = time.monotonic()

    user_text = update.message.text
    if not user_text:
        return

    if len(user_text) > _MAX_MESSAGE_LENGTH:
        await update.message.reply_text(
            f"Poruka je predugačka (max {_MAX_MESSAGE_LENGTH} karaktera). "
            "Molim vas skratite pitanje."
        )
        return

    if _is_rate_limited(chat_id):
        await update.message.reply_text(
            f"Previše poruka. Sačekajte {_RATE_WINDOW} sekundi pa pokušajte ponovo."
        )
        return

    _chat_history[chat_id].append({"role": "user", "content": user_text})
    _trim_history(chat_id)

    async def keep_typing():
        while True:
            await update.effective_chat.send_action(ChatAction.TYPING)
            await asyncio.sleep(4)

    typing_task = asyncio.create_task(keep_typing())

    status_msg = None
    try:
        status_msg = await update.message.reply_text(_STATUS_INITIAL)
    except Exception:
        logger.warning("failed to send status message to %s", chat_id, exc_info=True)

    last_status = _STATUS_INITIAL

    async def on_progress(round_num: int, tools: list[str]) -> None:
        nonlocal last_status
        if status_msg is None:
            return
        text = _status_for_tools(tools)
        if text == last_status:
            return  # editing with identical text raises BadRequest
        last_status = text
        await status_msg.edit_text(text)

    t0 = time.monotonic()
    try:
        messages = list(_chat_history[chat_id])
        result: AgentResponse = await agent_run(messages, on_progress=on_progress)

        reply, conf_level = _apply_disclaimer(result.text)
        clean_text = _strip_sentinel(result.text)

        history_text = clean_text
        if result.tools_used:
            history_text += f"\n\n[Korišćeni alati: {', '.join(result.tools_used)}]"
        _chat_history[chat_id].append({"role": "assistant", "content": history_text})

        elapsed = time.monotonic() - t0
        logger.info(
            "chat=%s latency=%.1fs rounds=%d tokens_in=%d tokens_out=%d "
            "web_search=%s sources=%d reply_len=%d conf=%s tools=%s",
            chat_id, elapsed, result.rounds,
            result.input_tokens, result.output_tokens,
            result.web_search_used, len(result.sources), len(clean_text),
            conf_level,
            ",".join(result.tools_used) or "none",
        )

        sources_html = _format_sources(result.sources) if result.sources else ""

        await _send_long(update, reply, sources_html)

        response_time_ms = int((time.monotonic() - t0) * 1000)
        asyncio.create_task(
            log_query(
                chat_id=chat_id,
                user_message=user_text,
                bot_response=clean_text,
                used_web_search=result.web_search_used,
                response_time_ms=response_time_ms,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
            )
        )

        if conf_level in _ESCALATE_LEVELS:
            reason = f"CONF:{conf_level} | {clean_text[:50]}"
            asyncio.create_task(log_escalation(chat_id, user_text, reason))
            asyncio.create_task(
                _notify_admins_escalation(context, chat_id, user_text, clean_text)
            )
    except Exception:
        logger.exception("Error processing message from %s", chat_id)
        # Remove orphaned user message — no assistant reply to pair with it,
        # which would corrupt the conversation context on subsequent calls.
        if _chat_history[chat_id] and _chat_history[chat_id][-1]["role"] == "user":
            _chat_history[chat_id].pop()
        await update.message.reply_text(
            "Došlo je do greške pri obradi vašeg pitanja. Pokušajte ponovo."
        )
    finally:
        typing_task.cancel()
        if status_msg is not None:
            try:
                await status_msg.delete()
            except Exception:
                logger.warning("failed to delete status message in %s", chat_id, exc_info=True)


async def document_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    if not _is_admin(chat_id):
        await update.message.reply_text("Nemate dozvolu za otpremanje dokumenata.")
        return

    doc = update.message.document
    if not doc.file_name.lower().endswith(".pdf"):
        await update.message.reply_text("Molim vas pošaljite PDF fajl.")
        return

    size_mb = (doc.file_size or 0) / (1024 * 1024)
    if size_mb > _MAX_PDF_SIZE_MB:
        await update.message.reply_text(
            f"PDF je prevelik ({size_mb:.1f} MB, max {_MAX_PDF_SIZE_MB} MB)."
        )
        return

    await update.message.reply_text(f"⏳ Obrađujem {doc.file_name}...")

    try:
        file = await context.bot.get_file(doc.file_id)
        safe_name = Path(doc.file_name).name
        PDF_DIR.mkdir(parents=True, exist_ok=True)
        pdf_path = PDF_DIR / safe_name
        await file.download_to_drive(str(pdf_path))

        with pymupdf.open(pdf_path) as _pdf:
            page_count = _pdf.page_count

        try:
            full_doc, chunks = process_single_pdf_with_doc(pdf_path)
        except ScannedPDFError:
            await update.message.reply_text(
                f"❌ {safe_name} — skenirani PDF, potreban je tekstualni PDF"
            )
            return
        if not chunks:
            await update.message.reply_text(
                f"❌ {safe_name} — nije moguće izvući tekst"
            )
            return
        if len(chunks) > _MAX_CHUNKS:
            await update.message.reply_text(
                f"❌ {safe_name} — dokument je prevelik ({len(chunks)} delova, "
                f"maksimalno {_MAX_CHUNKS}). Molim vas podelite PDF na manje dokumente."
            )
            return

        if USE_CONTEXTUAL:
            chunks = await contextualize_chunks(full_doc, chunks)

        embed_texts = [c.get("contextualized_text", c.get("embedding_text", c["text"])) for c in chunks]
        metadatas = [c["metadata"] for c in chunks]
        vectors = await embed_documents(embed_texts)
        original_texts = [c["text"] for c in chunks]

        await delete_by_filename(safe_name)
        count = await upsert_chunks(original_texts, vectors, metadatas)

        success_msg = f"✅ {safe_name} — {count} delova indeksirano"
        if page_count > _PAGE_WARN_THRESHOLD:
            success_msg += (
                f"\n\n⚠️ Napomena: PDF ima {page_count} stranica. "
                "Za najbolji kvalitet pretrage preporučujemo upload manjih dokumenata "
                f"(do {_PAGE_WARN_THRESHOLD} stranica po fajlu)."
            )
        await update.message.reply_text(success_msg)
    except UnexpectedResponse as e:
        msg_lower = str(e).lower()
        if e.status_code in (403, 507) or any(
            k in msg_lower for k in ("quota", "storage")
        ):
            logger.error("Qdrant quota hit on upload from %s: %s", chat_id, e)
            await update.message.reply_text(
                f"❌ {doc.file_name} — Qdrant skladište je puno. "
                "Kontaktirajte administratora da nadogradi plan ili oslobodi prostor."
            )
        else:
            logger.exception("Qdrant error processing PDF upload from %s", chat_id)
            await update.message.reply_text(
                f"❌ {doc.file_name} — greška pri obradi, pokušajte ponovo"
            )
    except Exception:
        logger.exception("Error processing PDF upload from %s", chat_id)
        await update.message.reply_text(
            f"❌ {doc.file_name} — greška pri obradi, pokušajte ponovo"
        )


def _format_stats(stats: dict, header: str) -> str:
    """Format stats dict into Serbian text."""
    lines = [header, ""]

    q = stats["question_count"]
    wow = stats["week_over_week_change"]
    if wow is not None:
        sign = "+" if wow >= 0 else ""
        lines.append(f"Pitanja: {q} ({sign}{wow}% vs prosli period)")
    else:
        lines.append(f"Pitanja: {q}")

    answered = stats["answered_count"]
    esc = stats["escalation_count"]
    total = answered + esc if (answered + esc) > 0 else 1
    ans_pct = round(answered / total * 100)
    esc_pct = round(esc / total * 100)
    lines.append(f"Odgovoreno: {answered} ({ans_pct}%)")
    lines.append(f"Eskalacije: {esc} ({esc_pct}%)")
    lines.append("")
    lines.append(f"Korisnici: {stats['unique_users']} jedinstvenih")

    recent_esc = stats["recent_escalations"]
    if recent_esc:
        lines.append("")
        lines.append("Poslednje eskalacije:")
        for i, r in enumerate(recent_esc, 1):
            ts = r["created_at"][:16].replace("T", " ") if r["created_at"] else ""
            lines.append(f"  {i}. {r['reason']} ({ts})")

    avg_s = stats["avg_response_time_ms"] / 1000 if stats["avg_response_time_ms"] else 0
    lines.append("")
    lines.append(f"Prosecno vreme odgovora: {avg_s:.1f}s")

    return "\n".join(lines)


async def stats_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin-only /stats command. Usage: /stats [days]"""
    chat_id = update.effective_chat.id
    if not _is_admin(chat_id):
        await update.message.reply_text("Nemate pristup ovoj komandi.")
        return

    days = 7
    if context.args:
        try:
            days = int(context.args[0])
            if days < 1 or days > 365:
                days = 7
        except ValueError:
            days = 7

    try:
        stats = await get_stats(days=days)
        header = f"Statistika \u2014 poslednjih {days} dana"
        text = _format_stats(stats, header)
        await update.message.reply_text(text)
    except Exception:
        logger.exception("Error fetching stats for chat_id=%s", chat_id)
        await update.message.reply_text("Greska pri ucitavanju statistike.")


_QUESTION_PREVIEW_MAX = 200


def _format_questions_digest(rows: list[dict], days: int) -> str:
    """Render a list of questions as HTML bullets: '<pitanje> (<korisnik>)'."""
    header = f"<b>Pitanja u poslednjih {days} dana ({len(rows)}):</b>"
    if not rows:
        return header + "\n\nNema pitanja u ovom periodu."

    lines = [header, ""]
    for r in rows:
        msg = r["user_message"] or ""
        if len(msg) > _QUESTION_PREVIEW_MAX:
            msg = msg[:_QUESTION_PREVIEW_MAX] + "…"
        name = acl.get_name(r["chat_id"]) or f"ID {r['chat_id']}"
        lines.append(f"• {html.escape(msg)} (<i>{html.escape(name)}</i>)")
    return "\n".join(lines)


def _format_questions_digest_plain(rows: list[dict], days: int) -> str:
    """Plain-text digest for chat-history injection (no HTML, no escaping).

    Used so follow-up admin questions ("sumiraj top pitanja") can reason over
    the data without re-fetching. Mirrors _format_questions_digest output.
    """
    header = f"Pitanja u poslednjih {days} dana ({len(rows)}):"
    if not rows:
        return header + "\n\nNema pitanja u ovom periodu."

    lines = [header, ""]
    for r in rows:
        msg = r["user_message"] or ""
        if len(msg) > _QUESTION_PREVIEW_MAX:
            msg = msg[:_QUESTION_PREVIEW_MAX] + "…"
        name = acl.get_name(r["chat_id"]) or f"ID {r['chat_id']}"
        lines.append(f"• {msg} ({name})")
    return "\n".join(lines)


async def pitanja_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin-only /pitanja [dana] — list questions from the last N days."""
    chat_id = update.effective_chat.id
    if not _is_admin(chat_id):
        await update.message.reply_text("Nemate pristup ovoj komandi.")
        return

    days = 7
    if context.args:
        try:
            days = int(context.args[0])
        except ValueError:
            await update.message.reply_text("Broj dana mora biti ceo broj (1-90).")
            return
        if days < 1 or days > 90:
            await update.message.reply_text("Broj dana mora biti između 1 i 90.")
            return

    try:
        rows = await get_recent_questions(days=days)
    except Exception:
        logger.exception("Error fetching questions for chat_id=%s", chat_id)
        await update.message.reply_text("Greška pri učitavanju pitanja.")
        return

    text = _format_questions_digest(rows, days)
    for chunk in _split_html(text, 4096):
        try:
            await update.message.reply_text(chunk, parse_mode=ParseMode.HTML)
        except Exception:
            await update.message.reply_text(chunk)

    # Inject digest into chat history so follow-ups like "sumiraj top pitanja"
    # can reason over the data without re-querying analytics.
    arg_str = f" {context.args[0]}" if context.args else ""
    plain = _format_questions_digest_plain(rows, days)
    _chat_history[chat_id].append({"role": "user", "content": f"/pitanja{arg_str}"})
    _chat_history[chat_id].append({"role": "assistant", "content": plain})
    _trim_history(chat_id)
    _last_activity[chat_id] = time.monotonic()


async def _weekly_questions_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send last-week question list to all admins. Monday 09:00 Europe/Belgrade."""
    try:
        rows = await get_recent_questions(days=7)
        text = _format_questions_digest(rows, 7)
        chunks = _split_html(text, 4096)
        for admin_id in acl.get_admins():
            for chunk in chunks:
                try:
                    await context.bot.send_message(
                        chat_id=admin_id, text=chunk, parse_mode=ParseMode.HTML
                    )
                except Exception:
                    logger.exception(
                        "Failed to send weekly questions to admin %s", admin_id
                    )
                    break
    except Exception:
        logger.exception("Error generating weekly questions digest")



async def _set_bot_commands(bot) -> None:
    """Register command menus: default scope for users, per-chat scope for admins."""
    try:
        await bot.set_my_commands(_USER_COMMANDS, scope=BotCommandScopeDefault())
    except Exception:
        logger.warning("Failed to set default bot commands — Telegram API may be unavailable")
        return
    for admin_id in acl.get_admins():
        try:
            await bot.set_my_commands(
                _ADMIN_COMMANDS, scope=BotCommandScopeChat(admin_id)
            )
        except Exception:
            logger.warning("Failed to set commands for admin %s", admin_id)


async def _post_init(app: Application) -> None:
    await _set_bot_commands(app.bot)
    try:
        await app.bot.set_my_short_description(
            "Računovodstveni asistent agencije Mari-Mar."
        )
        await app.bot.set_my_description(
            "Zdravo! Pritisnite Start kako biste se registrovali i počeli razgovor."
        )
    except Exception:
        logger.warning("Failed to set bot description")
    try:
        logo = PDF_DIR.parent / "assets" / "bot-profile.jpg"
        if logo.exists():
            with open(logo, "rb") as f:
                await app.bot.set_my_profile_photo(InputProfilePhotoStatic(photo=f))
    except Exception:
        logger.warning("Failed to set bot profile photo")


def build_app() -> Application:
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).post_init(_post_init).build()
    acl.load()
    invites.load()
    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("help", help_handler))
    app.add_handler(CommandHandler("clear", clear_handler))
    app.add_handler(CommandHandler("stats", stats_handler))
    app.add_handler(CommandHandler("adduser", adduser_handler))
    app.add_handler(CommandHandler("removeuser", removeuser_handler))
    app.add_handler(CommandHandler("users", users_handler))
    app.add_handler(CommandHandler("addadmin", addadmin_handler))
    app.add_handler(CommandHandler("removeadmin", removeadmin_handler))
    app.add_handler(CommandHandler("dokumenti", dokumenti_handler))
    app.add_handler(CommandHandler("pitanja", pitanja_handler))
    app.add_handler(CommandHandler("broadcast", broadcast_handler))
    app.add_handler(CommandHandler("invite", invite_handler))
    app.add_handler(CallbackQueryHandler(_registration_callback, pattern=r"^reg_(approve|deny):"))
    app.add_handler(MessageHandler(filters.Document.ALL, document_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))

    # Periodic sweep of inactive chat histories and rate-limit entries
    job_queue = app.job_queue
    if job_queue is not None:
        job_queue.run_repeating(_sweep_job, interval=3600, first=3600, name="sweep_inactive")
        # Monday 09:00 Europe/Belgrade — PTB uses Monday=0 in days tuple.
        job_queue.run_daily(
            _weekly_questions_job,
            time=dtime(hour=9, minute=0, tzinfo=ZoneInfo("Europe/Belgrade")),
            days=(0,),
            name="weekly_questions",
        )

    return app
