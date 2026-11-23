# CLAUDE.md

Serbian-language accounting RAG Telegram bot. Claude Sonnet 5.5 agentic loop (native web search + Anthropic sandboxed code execution + Qdrant vector search over accounting PDFs). Cohere embed-v4.0 for embeddings. All user-facing text in Serbian.

**Client constraint (Marko, agency owner): user-facing text must never reveal internet/web usage** — no "internet", "web", "online" in statuses, replies, or sources. Enforced by: `_format_sources` omits web links, progress statuses use generic wording, `tests/test_progress_status.py::test_no_status_mentions_internet` guards the strings.

## Commands

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python bot.py                       # Run Telegram bot (polling mode)
python ingest.py                    # Ingest PDFs from data/pdfs/ (safe — stops if collection exists)
python ingest.py --force            # Recreate collection (destructive — deletes all existing vectors)
python ingest.py /path/to/pdfs      # Ingest from custom dir (combine with --force if needed)
.venv/bin/pytest tests/ -v          # Unit tests (pytest)
docker-compose build && docker-compose down && docker-compose up -d  # Local dev (v1: down+up avoids ContainerConfig bug)
docker-compose logs --tail=30       # Check logs

./scripts/deploy.sh                 # Deploy to Hetzner prod: HEAD==origin/main check → ssh git pull → docker compose up -d --build → health verify
./scripts/sync-data.sh acl.json invites.json analytics.db  # Migrate runtime state to prod (.db files: sqlite3.backup + atomic swap, ~5s downtime)
ssh deploy@178.105.109.233          # Prod server (non-root deploy user; root@ for system admin only)
```

## Architecture

Two entry points: `bot.py` (Telegram bot + health server + Qdrant keepalive) and `ingest.py` (batch PDF indexing). Deployed via Docker Compose.

### Agent (`src/agent.py`)
- Claude tool-use loop, max rounds via `config.yaml` `agent.max_tool_rounds`, per-call `max_tokens` via `agent.max_tokens` (default 8192), effort via `agent.effort` (`output_config.effort`, passed through `extra_body` so an older cached SDK in the prod image can't TypeError on an unknown kwarg; unset = API default `high`).
- Two declared tools: `search_documents` (custom: embed query via Cohere → Qdrant vector search) and `web_search` (`web_search_20260209`, Claude native, max 3 uses). Code execution is **auto-injected** by the API when `web_search_20260209` is present — do NOT declare it explicitly or the API returns 400 ("Auto-injecting tools would conflict with existing tool names"). The auto-injected code execution is general-purpose (math, data analysis), not limited to web search filtering. Free when paired with web_search.
- `agent.run(messages, on_progress=None) → AgentResponse(text, sources, web_search_used, tools_used, input_tokens, output_tokens, rounds)`. Decoupled from frontend. `on_progress(round_num, tools_this_round)` is an optional async callback fired after each **non-terminal** round (drives Telegram status edits); callback errors are logged and never break the loop. Server-side tools in the final (`end_turn`) round produce NO progress event — they complete inside one API call (222s single-round turns observed in prod); the handler's rotation timer covers that gap.
- Terminal stop reasons: `end_turn`, `max_tokens`, `refusal`, `stop_sequence`, `model_context_window_exceeded` — looping after any of these would resend a history ending on an assistant turn (API rejects as prefill). Empty text falls back to "Nisam uspeo da pronađem odgovor."
- `search_documents` failure (Qdrant/Cohere outage) degrades gracefully: returns a tool-error JSON telling the model to fall back to web search instead of killing the reply.
- Server-side tools (code_execution, web_search) are handled by the API — their results arrive in `response.content` as `server_tool_use` / `*_tool_result` blocks, no `tool_result` message needed. Only `search_documents` requires client-side handling.
- **Server-side tool block types**: `server_tool_use` blocks have `name` = `"bash_code_execution"` | `"text_editor_code_execution"` | `"web_search"` (never `"code_execution"` directly). Result blocks: `bash_code_execution_tool_result`, `text_editor_code_execution_tool_result`, `web_search_tool_result`. All code execution sub-tool names are normalized to `"code_execution"` in `tools_used` tracking. Both the `end_turn` and non-`end_turn` code paths must scan for these blocks — server-side tools complete within a single API response and return `end_turn`.
- `pause_turn` stop reason: resend the history **as-is** — the API detects the trailing `server_tool_use` block and resumes automatically. Do NOT inject a "Continue." user message (breaks the resume). Pending client-side `tool_use` blocks are answered first (a `pause_turn` response can carry one).
- Chat history annotation: `tools_used` list is appended as `[Korišćeni alati: ...]` to the assistant message stored in `_chat_history` (for Claude context on follow-ups), but never sent to Telegram. The reply to Telegram always uses clean `result.text`.
- System prompt has date/time injected at load time (`_load_system_prompt()` appends current Belgrade datetime).
- Retry via SDK built-in `max_retries=5` on the `AsyncAnthropic` client.

### Ingestion (`src/pdf_loader.py` → `src/embeddings.py` → `src/vector_store.py`)
1. PyMuPDF4LLM extracts PDF → Markdown. Scanned/image PDFs raise `ScannedPDFError` (no OCR).
2. Cyrillic → Latin transliteration (`cyrtranslit`) at index time AND query time.
3. Two-stage chunking: MarkdownHeaderTextSplitter (h1/h2/h3), then RecursiveCharacterTextSplitter (2000 chars, 0 overlap, Serbian-aware separators: `". "`, `"; "`).
4. Each chunk dict: `text` (raw, stored in Qdrant), `embedding_text` (`"Izvor: {filename} | Sekcija: {breadcrumb}\n\n{text}"`), `metadata` (`filename`, `section` breadcrumb, `parent_text`).
5. Embed text selection priority: `contextualized_text` > `embedding_text` > `text`. Storage text is always raw `text`.
6. Cohere embed-v4.0 (1024 dims, `input_type="search_document"` for indexing, `"search_query"` for queries).
7. Qdrant upsert with full metadata. COSINE distance. `filename` payload index (KEYWORD) for filtered deletion.

`ingest.py` is **safe by default** — checks `collection_exists()` and stops if the collection is already populated. Pass `--force` to recreate (destructive). Telegram PDF upload replaces via **versioned upsert**: new chunks upsert first tagged with a `uuid` upload version, then `delete_stale_versions()` sweeps old points — a failed upload sweeps its own partial points (`delete_upload_version`) and never destroys the existing document.

### Vector Store (`src/vector_store.py`)
- `search()` filters results below `min_score` (config.yaml, default 0.35). Prevents feeding Claude noise from low-relevance chunks.
- Versioned replace: `delete_stale_versions(filename, keep_version)` (sweep old points after successful upsert) + `delete_upload_version(filename, upload_version)` (clean up a failed upload's partial points). `delete_by_filename(filename)` still exists but has no callers — legacy.
- `list_documents()` — scrolls all points, returns `dict[str, int]` of `{filename: chunk_count}`. Used by `/dokumenti`.
- Both `ensure_collection()` and `recreate_collection()` create the `filename` KEYWORD payload index.

### ACL (`src/acl.py`)
- JSON-backed access control at `data/acl.json`. Seeds from `config.yaml` `admin_chat_ids`/`allowed_chat_ids` when the file doesn't exist, then independent.
- **`data/acl.json` is gitignored** (runtime state — mutated by `/adduser` etc.). `data/acl.example.json` is the tracked empty template. Tracking it caused merge conflicts on every deploy when ACL diverged — don't re-track it.
- Mutations persist immediately — no restart needed to add/remove users or admins.
- Admins are implicitly allowed — no need to also add them to `allowed_chat_ids`.
- **Fail-OPEN by design**: `is_allowed()` returns True for everyone when `allowed_chat_ids` is empty (`config.yaml` ships it empty). So if `data/acl.json` is ever lost (fresh clone before `sync-data.sh`, accidental `rm`/`git clean`, restore from a pre-users backup) the bot answers anyone — admin functions stay gated (admin list seeds from `config.yaml`), but the corpus is generic accounting PDFs (no client-confidential data) and the only real cost is burned API quota. Accepted tradeoff (not fixed to fail-closed). Mitigation: on a fresh deploy run `sync-data.sh acl.json` *before* first start; if acl.json vanishes on a live box, restore from `/var/backups/mari-mar-bot/` immediately. Documented in `docs/RUNBOOK.md`.

### Invites & Registrations (`src/invites.py`)
- JSON-backed store at `data/invites.json`. Same atomic tmp→rename persistence as `acl.py`. Three sections: `pending` (invite tokens), `used` (consumed tokens — audit trail, pruned after 30 days), `registrations` (pending approval requests, 24h TTL — expiry also checked at read time in `get_registration()`, since save-time pruning alone would let an ignored request block re-registration forever).
- **`data/invites.json` is gitignored** (runtime state); `data/invites.example.json` is the tracked empty template. `invites.py` `load()` initializes an empty store when the file is missing.
- **Invite tokens (Flow B):** `create(label, created_by, ttl_days=7) → token` — `secrets.token_urlsafe(16)` (128-bit). `consume(token, chat_id) → dict | None` — atomic pop+expiry check under single-threaded asyncio.
- **Registrations (Flow A):** `add_registration(chat_id, name, company) → bool` (False if already pending — rate limit). `get_registration(chat_id)`, `resolve_registration(chat_id, approved_by)`, `remove_registration(chat_id)`. One pending per chat_id.

### Telegram Handler (`src/telegram_handler.py`)
- Polling mode, per-chat history in memory (max 5 exchanges, lost on restart). Periodic sweep (hourly) evicts entries idle > `inactive_ttl_minutes` (default 60). `_trim_history` guarantees the trimmed list starts with a user turn (a leading assistant entry 400s every API call until /clear).
- **Progress status message**: on each question the bot sends `_STATUS_INITIAL` ("⏳ Analiziram vaše pitanje…") and edits it in place — per-round via `agent.run`'s `on_progress` (tool → text mapping in `_STATUS_BY_TOOL`, `search_documents` wins ties), plus a rotation timer (`_STATUS_ROTATION`, every `_STATUS_ROTATE_INTERVAL_S`=30s of no updates) covering long single-round server-side tool turns. Deleted in `finally` on success and error. All edit/delete failures degrade silently (warning log). Wording must respect the no-internet-mention constraint; consecutive edits must differ (Telegram 400s on identical edit_text).
- Fire-and-forget tasks MUST go through `_spawn()` (holds a strong ref until done) — bare `asyncio.create_task` results can be GC'd mid-flight and silently dropped.
- PyMuPDF is not thread-safe: PDF parsing is serialized via `_pdf_parse_lock` while running off the event loop.
- ACL via `src/acl.py` → `data/acl.json`. `allowed_chat_ids` (empty = allow all), `admin_chat_ids` (PDF upload + /stats + user management).
- **Bot profile** set at startup via `_post_init`: short description, empty-chat description (registration prompt), and scoped command menus.
- **Scoped command menus**: `_USER_COMMANDS` (4 commands) shown to all users, `_ADMIN_COMMANDS` (13 commands) shown per-admin via `BotCommandScopeChat`. Refreshed on `/addadmin` (set) and `/removeadmin` (delete, falls back to default). All API failures are non-fatal (warning log, bot continues).
- **Onboarding** — three paths, all converging on `acl.add_user()`:
  - **Flow B (invite link, preferred):** admin runs `/invite <name, company>` → bot returns `t.me/<bot>?start=<token>` (7-day, single-use). Client taps link → `invites.consume()` → auto-approved. Admin notified via `_notify_admins_invite_used()`.
  - **Flow A (registration form, fallback):** stranger sends `/start` without token → bot asks name → asks company → `invites.add_registration()` → admins get inline ✅/❌ buttons via `_notify_admins_registration()` + `CallbackQueryHandler`. Approve calls `acl.add_user()`, deny notifies user. One pending per chat_id. Messages during PENDING get "zahtev na čekanju" response. In-memory form state (`_registration_state` dict) evicted after 30 min by `_sweep_inactive()`.
  - **Manual:** admin runs `/adduser <ID> [name]` directly (existing, unchanged).
- `/clear` — reset chat history. `/adduser`, `/removeuser`, `/users`, `/addadmin`, `/removeadmin` — admin-only. Self-removal as admin blocked.
- `/dokumenti` — lists indexed PDFs from Qdrant. All users see filenames; admins also see chunk counts. Uses `_split_html` for long lists.
- `/broadcast <msg>` — admin-only. Rejects messages >4096 chars. Runs via `asyncio.create_task` (non-blocking). Sends to all users+admins (deduplicated), HTML with plain text fallback, `Forbidden` tracked separately. Admin gets interim "⏳" then final summary.
- Confidence sentinel + disclaimer: every LLM response ends with `[CONF:HIGH|MED|LOW|NONE|NA]` (regex `_CONF_PATTERN`). `_apply_disclaimer()` strips the marker and appends a fixed Mari-Mar disclaimer + `_CONTACT_BLOCK` (phone numbers); `NA` = out-of-scope, no disclaimer. Default fallback when sentinel missing = LOW (cautious posture, logs a warning). `_strip_sentinel()` is used for chat history + analytics so the LLM doesn't anchor on past confidence labels in follow-ups. Disclaimer texts and contact block are module constants in `telegram_handler.py` — tune wording/phone numbers there, not in the prompt.
- Escalation: fires when `conf_level in _ESCALATE_LEVELS` (currently `{LOW, NONE}`). Fire-and-forget `log_escalation()` (reason prefixed `CONF:<level> | <preview>`) + `_notify_admins_escalation()` sending user question + bot response preview to all admins. To narrow the trigger (e.g., NONE only) edit the frozenset; to widen (e.g., MED too) extend it.
- Admin PDF upload: download → read page count (PyMuPDF, under `_pdf_parse_lock`) → extract → chunk → hard-block if `len(chunks) > _MAX_CHUNKS` (600, rejects before embedding spend) → optional contextualization (`contextual_embeddings.enabled` in config.yaml, default false) → embed → versioned upsert → sweep stale versions. Distinct error branches: scanned PDF, empty extraction, chunk overflow, Qdrant quota (`UnexpectedResponse` with status 403/507 or body containing "quota"/"storage") → "Qdrant skladište je puno" message, generic fallback. Success message appends a soft page-count warning when `page_count > _PAGE_WARN_THRESHOLD` (50). Thresholds are module constants near `_MAX_PDF_SIZE_MB` — tune there.
- `/stats [days]` — admin analytics.
- `/pitanja [days]` — admin-only. Lists every user question from the last N days (default 7, max 90) as `• <pitanje> (<ime or ID ...>)`. Uses `_split_html` for long lists. Questions truncated at `_QUESTION_PREVIEW_MAX` (200 chars). Also sent automatically every Monday 09:00 Europe/Belgrade by `_weekly_questions_job` (registered via `JobQueue.run_daily`, `days=(0,)`). After replying, it also injects the digest as a plain-text assistant turn into `_chat_history` (`_format_questions_digest_plain`) so admin follow-ups like "sumiraj top pitanja" can analyze it without re-querying — the system prompt has a matching out-of-scope exception that ends such answers with `[CONF:NA]`.
- Markdown → HTML conversion. `_split_html` splits at 4096 chars respecting HTML tag boundaries, cutting with 64-char headroom so appended closing tags can't push a chunk over Telegram's hard limit. `_send_long` substitutes a fallback sentence for empty text (`reply_text("")` raises).

### Analytics (`src/analytics.py`)
- SQLite with WAL mode at `data/analytics.db`. Fire-and-forget query logging.
- Timestamp cutoffs MUST use `_sql_ts()` (`YYYY-MM-DD HH:MM:SS`, UTC) — `created_at` is stored in SQLite `CURRENT_TIMESTAMP` format, and `isoformat()` strings compare incorrectly against it.
- `answered_count` = questions − escalations (escalated queries also get replies; counting both double-counts in the split).
- `get_stats(days)` returns: question_count, unique_users, escalation_count, recent_escalations (last 5, newest first with timestamps), avg_response_time, week-over-week change.
- `get_recent_questions(days)` returns list of `{chat_id, user_message, created_at}` for the last N days, ordered ASC. Powers `/pitanja` and the weekly questions job.
- `log_escalation()` fires from telegram_handler when the LLM emits `[CONF:LOW]` or `[CONF:NONE]`. Logs to `escalations` table with reason `CONF:<level> | <preview>` (truncated to 50 chars). Notifies all admins via Telegram with user question + bot response preview (500 chars). `get_stats().escalation_count` aggregates LOW + NONE together — split by parsing the `CONF:` prefix in `reason` if a breakdown becomes useful.
- `update_daily_stats()` exists but is not wired up — dead code. `get_stats()` queries live, making it unnecessary at current volume.

### Qdrant Keep-alive (`src/qdrant_keepalive.py`)
- Pings Qdrant every 6 hours to prevent free-tier cluster hibernation.

### Health Server (`bot.py`)
- aiohttp server on `:8080/health`. **Deep check**: probes Qdrant (`get_collections()`) + Cohere (`models.list(page_size=1)` — free metadata call, `check_api_key()` is deprecated since cohere 5.0), each 3s timeout. Returns `{"status": "ok"|"degraded", "services": {qdrant: {ok, info}, cohere: {ok, info}}}` — HTTP **200** if both OK, **503** if either fails. Result cached `_HEALTH_TTL_SECONDS` (60s) so the Dockerfile `HEALTHCHECK` (every 30s) doesn't hammer upstream APIs. Consumed by Docker healthcheck + UptimeRobot.
- `bot.py` imports `_client` from `vector_store` and `embeddings` for the probes, and registers a log filter dropping `aiohttp.server` `BadHttpMessage` tracebacks — Mirai-style scanners hit `:8080` with malformed CGI exploits; the parser rejects them, no actionable signal, just noise.

## Deployment

### Local (dev)
- `Dockerfile` — python:3.12-slim, `HEALTHCHECK` on `:8080/health` (interval 30s, timeout 5s, 3 retries), runs `bot.py`.
- `docker-compose.yml` — single `bot` service, `restart: unless-stopped`, mounts `data/`, `config.yaml`, `prompts/` as volumes. Port 8080 exposed. No healthcheck in compose (it's in the Dockerfile); `unless-stopped` restarts on crash/exit, NOT on `unhealthy`.
- Local dev uses `docker-compose` v1 (EOL) — `down`+`up` instead of just `up -d` to avoid `ContainerConfig` bug on recreate.

### Production (Hetzner — "MARI-MAR bot", first commercial deploy)
- Server: Hetzner CPX22, IPv4 `178.105.109.233`, Ubuntu 24.04, app at `/opt/mari-mar-bot` owned by non-root `deploy` user. Repo pulled via GitHub deploy key (read-only). Hetzner project `mari-mar-bot` (ID 14507723); HCLOUD token in `~/.config/hcloud/cli.toml` is read-only — reboot via `ssh root@... systemctl reboot`, not `hcloud`.
- Prod uses Docker (29.x) with the **`docker compose` v2 plugin** — `docker compose up -d --build` works normally on recreate (no v1 ContainerConfig bug). Log rotation via `/etc/docker/daemon.json` (json-file `max-size 10m`, `max-file 3`).
- Hardening: UFW (allow 22 + 8080), fail2ban (sshd jail), unattended-upgrades, SSH password auth off, TZ Europe/Belgrade.
- `.env` at `/opt/mari-mar-bot/.env` (chmod 600, gitignored — bot reads only ANTHROPIC/COHERE/QDRANT_URL/QDRANT_API_KEY/TELEGRAM_BOT_TOKEN). Rotate keys → `docker compose restart bot`.
- Backups: daily cron `tar` of `data/` → `/var/backups/mari-mar-bot/data-YYYY-MM-DD.tar.gz` (7-day retention, `/usr/local/bin/mari-mar-bot-backup.sh` + `/etc/cron.d/mari-mar-bot-backup`). Hetzner Cloud Backups disabled by client cost decision — the cron tar is the compensation; intentional, not oversight.
- **Update procedure: `./scripts/deploy.sh`** from the dev laptop (pre-flight checks local HEAD == origin/main, then ssh git pull --ff-only + `docker compose up -d --build` + health verify). Rollback: `ssh deploy@server "cd /opt/mari-mar-bot && git checkout <sha> && docker compose up -d --build"`. PDFs are NOT in the repo — re-upload via Telegram admin handler.
- `./scripts/sync-data.sh <files>` migrates runtime state from local `data/` to prod: plain JSON (acl.json, invites.json) transfer with no downtime; `.db` files use Python `sqlite3.backup` snapshot + atomic swap with bot stopped (~5s). Both scripts honor `MARI_MAR_BOT_SERVER` env override.
- Monitoring: UptimeRobot HTTP monitor on `:8080/health` (5-min interval, Telegram alert contact).
- Full ops detail (troubleshooting, restore, key rotation, offboarding) → `docs/RUNBOOK.md`. Client-facing prereq docs → `docs/CLIENT_PREREQUISITES_*.md` (Hetzner doc appendix has the deployment status table + backlog).

## Configuration

- `.env` (gitignored) — the bot reads exactly five vars: ANTHROPIC_API_KEY, COHERE_API_KEY, QDRANT_URL, QDRANT_API_KEY, TELEGRAM_BOT_TOKEN. Any extra vars are ignored (don't park ops creds like HCLOUD tokens here — see `~/.config/hcloud/cli.toml`).
- `config.yaml` — model (`claude-sonnet-5-5`), max_tool_rounds, max_tokens (8192), effort (`medium`, sent via `extra_body` — see `src/agent.py`), top_k, min_score (0.35), chunk_size (2000), embedding_dimensions, collection_name, rate limits, inactive_ttl_minutes (60), contextual_embeddings.enabled (false). Chat ID lists are seed values only; live ACL is `data/acl.json` (gitignored).
- `prompts/system.txt` — Serbian system prompt (editable without deploy). LLM must end every response with one of `[CONF:HIGH|MED|LOW|NONE|NA]`; the prompt forbids writing disclaimers, napomene, or Mari-Mar contact info — those are appended in code by `_apply_disclaimer()`. Editing disclaimer wording or phone numbers = edit `_DISCLAIMERS` / `_CONTACT_BLOCK` in `telegram_handler.py`, not the prompt.

## Key Constraints

- **2GB VPS**: no torch, no heavy ML. PyMuPDF chosen over unstructured/docling/marker.
- **Serbian language**: all UX in Serbian. Cyrillic↔Latin via cyrtranslit. Cohere embed-v4.0 for multilingual.
- **No framework**: raw Anthropic SDK. Claude's native server tools (`web_search_20260209` + auto-injected `code_execution`) require direct SDK usage.
- `langchain-text-splitters` is the sole LangChain dep (MarkdownHeaderTextSplitter + RecursiveCharacterTextSplitter only).
- **Never run `ingest.py --force`** or call `recreate_collection()` without explicit user approval. This deletes all indexed vectors — production data loss.

## Testing

- **pytest** (`tests/`): unit tests, no external services needed. Config in `pytest.ini`, `asyncio_mode = auto`.
- `tests/test_html_splitting.py`: `_split_html` tag-boundary splitting, `_md_to_html` conversion.
- `tests/test_chunking.py`: chunk_markdown contract (breadcrumbs, embedding_text, parent_text, size limits, transliteration).
- `tests/test_acl.py`: ACL persistence, seed-from-config, add/remove users and admins.
- `tests/test_vector_store.py`: `delete_by_filename` filter construction, no-match silent behavior, delete-then-upsert replacement flow, `list_documents` aggregation and pagination.
- `tests/test_tools_used.py`: `tools_used` annotation — verifies tool metadata lands in chat history but never in Telegram reply, annotation format, no-tools edge case.
- `tests/test_document_handler.py`: path traversal sanitization, safe filename in delete, page-count warning suffix, chunk-overflow hard block (asserts embed/upsert not called), Qdrant quota branch vs generic Qdrant error fallthrough. Thresholds referenced via `telegram_handler._MAX_CHUNKS` / `_PAGE_WARN_THRESHOLD` so tests track constant changes.
- `tests/test_invites.py`: invite token store + registration store — create, consume, expiry, double-consume, persistence, load, list_pending, used-entry pruning, registration CRUD, duplicate blocking, TTL pruning, backward compat.
- `tests/test_questions_digest.py`: `get_recent_questions` window filtering, `_format_questions_digest` (HTML escape, truncation, named/unknown users), `_format_questions_digest_plain` (no HTML), `pitanja_handler` chat-history injection (default days, no injection on fetch error).
- `tests/test_progress_status.py`: status↔tool mapping, no-internet-wording guard, `on_progress` invocation + error resilience, status message lifecycle (sent → edited → deleted, deleted on agent failure, answer survives status-send failure), 30s rotation fires on long silent calls and never on fast queries.
- `tests/test_history_trim.py`: trimmed history never starts with an assistant turn.
- `tests/test_agent_effort.py`: `agent.effort` is forwarded as `extra_body.output_config.effort` (never the typed kwarg), nothing sent when unset, shipped config is `medium`.

## Known Issues

- `min_score` threshold (0.35) was calibrated on a 12-chunk / 2-PDF corpus. Re-evaluate when corpus grows significantly — score distributions will shift.
- Command definitions live in 3 places: `_USER_COMMANDS`/`_ADMIN_COMMANDS` (menu), `help_handler` (help text), `build_app()` (handler registration). Adding a command requires updating all three — no compile-time check. Accepted tradeoff at current command count.
- Stale command scopes: if an admin is removed while bot is down, their Telegram menu retains admin commands until next bot restart (startup only sets scopes for current admins, doesn't clean up former ones).
- `/health` deep check has no consecutive-failure debounce — a single transient Qdrant/Cohere blip flips it to 503 and can trip a UptimeRobot alert. The 60s result cache dampens flapping but doesn't eliminate it. Accepted tradeoff; revisit if alerts get noisy.
- **PTB processes updates sequentially across ALL chats** (`Application.builder()` without `concurrent_updates`) — one 200s+ query blocks every other user's message until it finishes. Fine at current user count; revisit before onboarding many firms.
- Non-streaming API calls give zero progress signal during long single-round server-side tool turns (222s observed); the 30s status rotation masks the symptom. Proper fix: switch `agent.run` to `client.messages.stream()` and fire `on_progress` from `content_block_start` events — also derisks the SDK's 10-min non-streaming timeout. Backlog.
- Very short user messages ("ok", "?") can yield replies without the `[CONF:*]` sentinel → default LOW → an admin escalation per message (observed in prod 2026-07-10). Watch for spam; fix candidates: NONE-only escalation, or min-question-length gate.
