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
    InputProfilePhotoStatic,
    Update,
)
from telegram.constants import ChatAction, ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from telegram.error import BadRequest, Forbidden

import pymupdf
from qdrant_client.http.exceptions import UnexpectedResponse

from src import acl, vector_store
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

# Escalation detection — must match the exact phrase from prompts/system.txt
_ESCALATION_MARKER = "preporučujemo da kontaktirate mari-mar.rs za direktnu konsultaciju"

_USER_COMMANDS = [
    BotCommand("start", "Pozdravna poruka i vaš ID"),
    BotCommand("help", "Prikaži listu komandi"),
    BotCommand("clear", "Obriši istoriju razgovora"),
    BotCommand("dokumenti", "Prikaži dokumente u bazi znanja"),
]

_ADMIN_COMMANDS = _USER_COMMANDS + [
    BotCommand("adduser", "Dodaj korisnika"),
    BotCommand("removeuser", "Ukloni korisnika"),
    BotCommand("users", "Prikaži listu korisnika"),
    BotCommand("addadmin", "Dodaj admina"),
    BotCommand("removeadmin", "Ukloni admina"),
    BotCommand("stats", "Statistika korišćenja"),
    BotCommand("pitanja", "Lista pitanja iz prethodnog perioda"),
    BotCommand("broadcast", "Pošalji obaveštenje svima"),
]

_chat_history: dict[int, list[dict]] = defaultdict(list)
_rate_timestamps: dict[int, list[float]] = defaultdict(list)
_last_activity: dict[int, float] = {}


def _sweep_inactive() -> None:
    """Remove history and rate-limit entries for chats idle longer than TTL."""
    now = time.monotonic()
    stale = [cid for cid, ts in _last_activity.items() if now - ts > _INACTIVE_TTL_S]
    for cid in stale:
        _chat_history.pop(cid, None)
        _rate_timestamps.pop(cid, None)
        _last_activity.pop(cid, None)
    if stale:
        logger.info("Swept %d inactive chat(s)", len(stale))


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


def _display_id(chat_id: int) -> str:
    """Format chat ID with name if known: 'Name (ID)' or just 'ID'."""
    name = acl.get_name(chat_id)
    return f"{name} ({chat_id})" if name else str(chat_id)


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
    await update.message.reply_text(
        f"Zdravo! 👋\n\n"
        f"Ja sam računovodstveni asistent kompanije Mari-Mar. "
        f"Pomažem sa pitanjima iz oblasti poreza, računovodstva i poslovnih propisa.\n\n"
        f"Možete me pitati, na primer:\n"
        f'• \u201eDo kada se podnosi prijava za godišnji porez na dohodak za 2025?\u201c\n'
        f'• \u201eKoja je razlika između preduzetnika i DOO u pogledu poreza?\u201c\n'
        f'• \u201eKoji je trenutni iznos minimalne zarade u Srbiji?\u201c\n\n'
        f"Vaš korisnički ID: {chat_id}\n"
        f"Prosledite ovaj ID kompaniji Mari-Mar kako biste bili dodati na listu korisnika."
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


async def help_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id

    lines = [
        "<b>Dostupne komande:</b>\n",
        "/start — Pozdravna poruka i vaš korisnički ID",
        "/clear — Obriši istoriju razgovora",
        "/help — Prikaži listu komandi",
        "/dokumenti — Prikaži dokumente u bazi znanja",
    ]

    if _is_admin(chat_id):
        lines.append("\n<b>Admin komande:</b>\n")
        lines.append("/adduser &lt;ID&gt; [ime] — Dodaj korisnika")
        lines.append("/removeuser &lt;ID&gt; — Ukloni korisnika")
        lines.append("/users — Prikaži listu korisnika")
        lines.append("/addadmin &lt;ID&gt; [ime] — Dodaj admina")
        lines.append("/removeadmin &lt;ID&gt; — Ukloni admina")
        lines.append("/stats [dana] — Statistika (podrazumevano 7 dana)")
        lines.append("/pitanja [dana] — Lista pitanja (podrazumevano 7 dana, max 90)")
        lines.append("/broadcast &lt;poruka&gt; — Pošalji obaveštenje svim korisnicima")
        lines.append("\n📄 Pošaljite PDF fajl da biste ga dodali u bazu znanja.")

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def dokumenti_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    chat_id = update.effective_chat.id
    if not _is_allowed(chat_id):
        await update.message.reply_text("Nemate pristup ovom botu.")
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
        await update.message.reply_text("Upotreba: /broadcast &lt;poruka&gt;")
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
    if not _is_allowed(chat_id):
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
    t0 = time.monotonic()
    try:
        messages = list(_chat_history[chat_id])
        result: AgentResponse = await agent_run(messages)

        _chat_history[chat_id].append({"role": "assistant", "content": result.text})

        elapsed = time.monotonic() - t0
        logger.info(
            "chat=%s latency=%.1fs rounds=%d tokens_in=%d tokens_out=%d "
            "web_search=%s sources=%d reply_len=%d",
            chat_id, elapsed, result.rounds,
            result.input_tokens, result.output_tokens,
            result.web_search_used, len(result.sources), len(result.text),
        )

        reply = result.text
        sources_html = _format_sources(result.sources) if result.sources else ""

        await _send_long(update, reply, sources_html)

        response_time_ms = int((time.monotonic() - t0) * 1000)
        asyncio.create_task(
            log_query(
                chat_id=chat_id,
                user_message=user_text,
                bot_response=result.text,
                used_web_search=result.web_search_used,
                response_time_ms=response_time_ms,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
            )
        )

        if _ESCALATION_MARKER in result.text.lower():
            asyncio.create_task(log_escalation(chat_id, user_text, result.text[:50]))
            asyncio.create_task(
                _notify_admins_escalation(context, chat_id, user_text, result.text)
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


async def _weekly_digest_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send weekly digest to all admins. Scheduled for Monday 09:00 Belgrade time."""
    try:
        stats = await get_stats(days=7)
        if stats["question_count"] < 2:
            logger.info("Weekly digest skipped: only %d questions", stats["question_count"])
            return

        now = datetime.now(ZoneInfo("Europe/Belgrade"))
        week_end = now.date()
        week_start = week_end - timedelta(days=7)
        header = f"Nedeljni pregled \u2014 sedmica od {week_start.strftime('%d.%m.')} do {week_end.strftime('%d.%m.')}"
        text = _format_stats(stats, header)

        for admin_id in acl.get_admins():
            try:
                await context.bot.send_message(chat_id=admin_id, text=text)
            except Exception:
                logger.exception("Failed to send weekly digest to admin %s", admin_id)
    except Exception:
        logger.exception("Error generating weekly digest")


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
