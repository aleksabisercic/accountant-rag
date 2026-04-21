# CLAUDE.md

Serbian-language accounting RAG Telegram bot. Claude Sonnet 4.6 agentic loop (native web search + Anthropic sandboxed code execution + Qdrant vector search over accounting PDFs). Cohere embed-v4.0 for embeddings. All user-facing text in Serbian.

## Commands

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python bot.py                       # Run Telegram bot (polling mode)
python ingest.py                    # Ingest PDFs from data/pdfs/ (safe — stops if collection exists)
python ingest.py --force            # Recreate collection (destructive — deletes all existing vectors)
python ingest.py /path/to/pdfs      # Ingest from custom dir (combine with --force if needed)
python review_pdf_output.py         # Review PDF parsing + chunking quality → data/review/
python test_local.py                # Local tests (no Qdrant needed)
.venv/bin/pytest tests/ -v          # Unit tests (pytest)
docker-compose build && docker-compose down && docker-compose up -d  # Deploy (v1: down+up avoids ContainerConfig bug)
docker-compose logs --tail=30       # Check logs
```

## Architecture

Two entry points: `bot.py` (Telegram bot + health server + Qdrant keepalive) and `ingest.py` (batch PDF indexing). Deployed via Docker Compose.

### Agent (`src/agent.py`)
- Claude tool-use loop, max rounds controlled by `config.yaml` `agent.max_tool_rounds`.
- Three tools: `search_documents` (custom: embed query via Cohere → Qdrant vector search), `code_execution` (Anthropic server-side sandbox: `code_execution_20250825`, Python 3.11 with numpy/scipy/pandas/sympy, no network, no local resource cost), and `web_search` (`web_search_20260209`, Claude native, max 3 uses). Code execution is free when paired with web_search.
- `agent.run(messages: list[dict]) → AgentResponse(text, sources, web_search_used, tools_used, input_tokens, output_tokens, rounds)`. Decoupled from frontend.
- Server-side tools (code_execution, web_search) are handled by the API — their results arrive in `response.content` as `server_tool_use` / `*_tool_result` blocks, no `tool_result` message needed. Only `search_documents` requires client-side handling.
- `pause_turn` stop reason: code execution may pause for long-running turns — the loop sends a continuation message to resume.
- Chat history annotation: `tools_used` list is appended as `[Korišćeni alati: ...]` to the assistant message stored in `_chat_history` (for Claude context on follow-ups), but never sent to Telegram. The reply to Telegram always uses clean `result.text`.
- Retry with exponential backoff on Anthropic rate limit / 500 / connection errors.

### Ingestion (`src/pdf_loader.py` → `src/embeddings.py` → `src/vector_store.py`)
1. PyMuPDF4LLM extracts PDF → Markdown. Scanned/image PDFs raise `ScannedPDFError` (no OCR).
2. Cyrillic → Latin transliteration (`cyrtranslit`) at index time AND query time.
3. Two-stage chunking: MarkdownHeaderTextSplitter (h1/h2/h3), then RecursiveCharacterTextSplitter (2000 chars, 0 overlap, Serbian-aware separators: `". "`, `"; "`).
4. Each chunk dict: `text` (raw, stored in Qdrant), `embedding_text` (`"Izvor: {filename} | Sekcija: {breadcrumb}\n\n{text}"`), `metadata` (`filename`, `section` breadcrumb, `parent_text`).
5. Embed text selection priority: `contextualized_text` > `embedding_text` > `text`. Storage text is always raw `text`.
6. Cohere embed-v4.0 (1024 dims, `input_type="search_document"` for indexing, `"search_query"` for queries).
7. Qdrant upsert with full metadata. COSINE distance. `filename` payload index (KEYWORD) for filtered deletion.

`ingest.py` is **safe by default** — checks `collection_exists()` and stops if the collection is already populated. Pass `--force` to recreate (destructive). Telegram PDF upload is additive — deletes old chunks by filename, then upserts (replacement, not duplication).

### Vector Store (`src/vector_store.py`)
- `search()` filters results below `min_score` (config.yaml, default 0.35). Prevents feeding Claude noise from low-relevance chunks.
- `delete_by_filename(filename)` — removes all points matching a filename via Qdrant FilterSelector. Used by document_handler before upsert to replace PDFs without duplication.
- `list_documents()` — scrolls all points, returns `dict[str, int]` of `{filename: chunk_count}`. Used by `/dokumenti`.
- Both `ensure_collection()` and `recreate_collection()` create the `filename` KEYWORD payload index.

### ACL (`src/acl.py`)
- JSON-backed access control at `data/acl.json`. Seeds from `config.yaml` on first run, then independent.
- Mutations persist immediately — no restart needed to add/remove users or admins.
- Admins are implicitly allowed — no need to also add them to `allowed_chat_ids`.

### Telegram Handler (`src/telegram_handler.py`)
- Polling mode, per-chat history in memory (max 5 exchanges, lost on restart). Periodic sweep (hourly) evicts entries idle > `inactive_ttl_minutes` (default 60).
- ACL via `src/acl.py` → `data/acl.json`. `allowed_chat_ids` (empty = allow all), `admin_chat_ids` (PDF upload + /stats + user management).
- **Bot profile** set at startup via `_post_init`: short description, empty-chat description (registration prompt), and scoped command menus.
- **Scoped command menus**: `_USER_COMMANDS` (4 commands) shown to all users, `_ADMIN_COMMANDS` (12 commands) shown per-admin via `BotCommandScopeChat`. Refreshed on `/addadmin` (set) and `/removeadmin` (delete, falls back to default). All API failures are non-fatal (warning log, bot continues).
- `/start` — welcome + Chat ID for onboarding. `/clear` — reset chat history. `/adduser`, `/removeuser`, `/users`, `/addadmin`, `/removeadmin` — admin-only. Self-removal as admin blocked.
- `/dokumenti` — lists indexed PDFs from Qdrant. All users see filenames; admins also see chunk counts. Uses `_split_html` for long lists.
- `/broadcast <msg>` — admin-only. Rejects messages >4096 chars. Runs via `asyncio.create_task` (non-blocking). Sends to all users+admins (deduplicated), HTML with plain text fallback, `Forbidden` tracked separately. Admin gets interim "⏳" then final summary.
- Escalation detection: after `agent_run()`, checks if response contains `_ESCALATION_MARKER` (lowercased match against the exact system prompt phrase). On match: fire-and-forget `log_escalation()` + `_notify_admins_escalation()` (sends user question + bot response preview to all admins).
- Admin PDF upload: download → read page count (PyMuPDF) → extract → chunk → hard-block if `len(chunks) > _MAX_CHUNKS` (600, rejects before embedding spend) → embed → delete old vectors by filename → upsert. Distinct error branches: scanned PDF, empty extraction, chunk overflow, Qdrant quota (`UnexpectedResponse` with status 403/507 or body containing "quota"/"storage") → "Qdrant skladište je puno" message, generic fallback. Success message appends a soft page-count warning when `page_count > _PAGE_WARN_THRESHOLD` (50). Thresholds are module constants near `_MAX_PDF_SIZE_MB` — tune there.
- `/stats [days]` — admin analytics.
- `/pitanja [days]` — admin-only. Lists every user question from the last N days (default 7, max 90) as `• <pitanje> (<ime or ID ...>)`. Uses `_split_html` for long lists. Questions truncated at `_QUESTION_PREVIEW_MAX` (200 chars). Also sent automatically every Monday 09:00 Europe/Belgrade by `_weekly_questions_job` (registered via `JobQueue.run_daily`, `days=(0,)`).
- Markdown → HTML conversion. `_split_html` splits at 4096 chars respecting HTML tag boundaries.

### Analytics (`src/analytics.py`)
- SQLite with WAL mode at `data/analytics.db`. Fire-and-forget query logging.
- `get_stats(days)` returns: question_count, unique_users, escalation_count, recent_escalations (last 5, newest first with timestamps), avg_response_time, week-over-week change.
- `get_recent_questions(days)` returns list of `{chat_id, user_message, created_at}` for the last N days, ordered ASC. Powers `/pitanja` and the weekly questions job.
- `log_escalation()` is wired up — triggered when Claude's response contains the escalation marker phrase. Logs to `escalations` table (reason truncated to 50 chars) and notifies all admins via Telegram with user question + bot response preview (500 chars).
- `update_daily_stats()` exists but is not wired up — dead code. `get_stats()` queries live, making it unnecessary at current volume.

### Qdrant Keep-alive (`src/qdrant_keepalive.py`)
- Pings Qdrant every 6 hours to prevent free-tier cluster hibernation.

## Deployment

- `Dockerfile` — python:3.12-slim, health check on `:8080/health`, runs `bot.py`.
- `docker-compose.yml` — single `bot` service, `unless-stopped`, mounts `data/`, `config.yaml`, `prompts/` as volumes. Port 8080 exposed.
- Uses `docker-compose` v1 (EOL, not `docker compose` plugin). Use `down`+`up` instead of just `up -d` to avoid `ContainerConfig` bug on recreate.

## Configuration

- `.env` — API keys: ANTHROPIC_API_KEY, COHERE_API_KEY, QDRANT_URL, QDRANT_API_KEY, TELEGRAM_BOT_TOKEN
- `config.yaml` — model, max_tool_rounds, top_k, min_score (0.35), chunk_size (2000), embedding_dimensions, collection_name, rate limits, inactive_ttl_minutes (60). Chat ID lists are seed values only; live ACL is `data/acl.json`.
- `prompts/system.txt` — Serbian system prompt (editable without deploy). Two-tier referral: "Preporučujemo da kontaktirate mari-mar.rs za direktnu konsultaciju." (escalation — can't answer) vs "obratite se vašem računovođi" (has answer, suggests professional for precision). Only the first phrase triggers escalation detection.

## Key Constraints

- **2GB VPS**: no torch, no heavy ML. PyMuPDF chosen over unstructured/docling/marker.
- **Serbian language**: all UX in Serbian. Cyrillic↔Latin via cyrtranslit. Cohere embed-v4.0 for multilingual.
- **No framework**: raw Anthropic SDK. Claude's native server tools (`web_search_20260209`, `code_execution_20250825`) require direct SDK usage.
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
- `test_local.py`: manual integration tests (PDF extraction, Cohere embeddings, agent). Requires API keys.

## Known Issues

- `min_score` threshold (0.35) was calibrated on a 12-chunk / 2-PDF corpus. Re-evaluate when corpus grows significantly — score distributions will shift.
- Command definitions live in 3 places: `_USER_COMMANDS`/`_ADMIN_COMMANDS` (menu), `help_handler` (help text), `build_app()` (handler registration). Adding a command requires updating all three — no compile-time check. Accepted tradeoff at current command count.
- Stale command scopes: if an admin is removed while bot is down, their Telegram menu retains admin commands until next bot restart (startup only sets scopes for current admins, doesn't clean up former ones).
