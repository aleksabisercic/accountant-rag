# Accountant RAG — Component Review & Assumptions Audit

## System Overview

Telegram bot for Serbian accounting Q&A. Claude Sonnet 4.6 agentic loop with two
tools: vector search (Cohere + Qdrant) over ingested PDFs, and native web search
for current info. Deployed as Docker container on a 2GB VPS.

**External dependencies**: Anthropic API, Cohere API, Qdrant Cloud, Telegram Bot
API. **Four failure domains**: any one of those going down degrades or kills the
bot.

---

## 1. PDF Ingestion (`src/pdf_loader.py`, `ingest.py`)

**What it does**: PDF → Markdown → Cyrillic→Latin → chunk by headers → recursive
split → embed → upsert to Qdrant.

**Explicit decisions**:

- PyMuPDF4LLM for extraction (no torch, fits 2GB VPS)
- Cyrillic→Latin transliteration at index time (and query time) to normalize
  search
- Two-stage chunking: Markdown headers first, then recursive split at 512 chars
  / 50 overlap
- Scanned PDFs are rejected, not OCR'd (Tesseract removed)

**Dead code**: `process_single_pdf()` line 81-82
(`if not md.strip(): return []`) is unreachable. `extract_markdown()` already
raises `ScannedPDFError` on empty text (line 36-37), so it never returns an
empty string. If it returns at all, `md.strip()` is guaranteed truthy.

**Implicit assumptions**:

- All PDFs are text-based, not scanned images
- 512-char chunks are adequate for Serbian accounting text. This is on the small
  side — Serbian words are longer than English. A single accounting
  article/clause can easily exceed 512 chars, meaning it gets split mid-sentence
- `transliterate_to_latin` is applied to the entire markdown including headers,
  table syntax, URLs. If a PDF has URLs or code, they'll be mangled
- `MarkdownHeaderTextSplitter` assumes PyMuPDF4LLM produces clean markdown with
  proper `#` headers. Real-world PDFs often produce messy markdown (e.g.,
  headers detected as bold text, no `#` markers), which means everything lands
  in one giant chunk that then gets recursively split with no section metadata
- Chunk overlap of 50 chars is ~1 sentence fragment in Serbian. Borderline
  useful

**Validate**:

- [ ] Run `ingest.py` on your actual PDF corpus and inspect chunk count + a
      sample of chunks. Check if section metadata is populated or mostly empty
- [ ] Check a few chunks for mid-sentence splits on important clauses (e.g., tax
      rate definitions)
- [ ] Verify that PyMuPDF4LLM actually produces `#`-style headers from your
      PDFs, not just bold text

---

## 2. Embeddings (`src/embeddings.py`)

**What it does**: Thin wrapper over Cohere embed-v4.0 async client. Batch size
96, 1024 dimensions. Separate `input_type` for documents vs queries.

**Explicit decisions**:

- Cohere embed-v4.0 for multilingual Serbian support
- 1024 dimensions (not 768 or 256) — higher fidelity but more storage/compute

**Implicit assumptions**:

- Cohere's Serbian language coverage is adequate for accounting/legal
  terminology. Cohere doesn't publish per-language benchmarks for embed-v4.0, so
  this is trust-based
- No retry logic — a Cohere timeout during ingestion or a query crashes the
  operation
- Module-level client instantiation: `_client = cohere.AsyncClientV2(...)` runs
  at import time. If `COHERE_API_KEY` is missing or wrong, the import succeeds
  but every call fails

**Validate**:

- [ ] **Test a few real queries in Serbian and inspect the top-k results for
      relevance. Especially test with Cyrillic input to confirm transliteration
      → embedding → retrieval works end-to-end**
- [ ] Test with a deliberately wrong query (e.g., English cooking terms) and
      confirm low relevance scores

---

## 3. Vector Store (`src/vector_store.py`)

**What it does**: Qdrant CRUD. COSINE distance, async client, batch upsert
(100), **top_k=10 search.** # TODO

**Explicit decisions**:

- COSINE distance (standard for normalized embeddings)
- UUID point IDs (no deduplication — re-ingesting same PDF creates duplicate
  vectors)

**Implicit assumptions**:

- **New client per call**: every `search()`, `upsert_chunks()`,
  `ensure_collection()` creates and destroys an `AsyncQdrantClient`. This means
  a new HTTPS connection per user query. At low traffic this is fine; at scale
  it adds ~50-100ms latency per search and hammers Qdrant Cloud connection
  limits
- `top_k=10` returns 10 results regardless of relevance score. No score
  threshold filtering — irrelevant results (score 0.3) are returned alongside
  relevant ones (score 0.9), and all 10 are stuffed into Claude's context
- `recreate_collection()` in `ingest.py` is destructive — nukes everything. But
  from X.pdf" for deletion/replacement

**Validate**:

- [ ] After ingesting your real corpus, run a few queries and check the scores
      of the 10 returned results. If the bottom 5 are all <0.5, you're feeding
      Claude noise

---

## 4. Agent / Inference (`src/agent.py`)

**What it does**: Claude tool-use agentic loop. Max 2 rounds. Two tools:
`search_documents` (custom) and `web_search_20250305` (Claude native, max 3
uses).

**Explicit decisions**:

- Claude Sonnet 4.6 (cost over capability trade-off)
- max_tokens=4096 per response
- Retry with exponential backoff for Anthropic API errors (rate limit, 500,
  connection)
- System prompt in Serbian, loaded from file (editable without code deploy) #
  TODO
- Sources tracked and deduped

**Implicit assumptions**:

- **`max_tool_rounds: 2` is very tight**. Round 1: Claude calls search_documents
  OR web_search. Round 2: Claude calls the other OR responds. This means Claude
  can do at most 1 search + 1 web search. If the first search returns poor
  results and Claude wants to reformulate and try again, it can't. The
  `config.example.yaml` had 5 rounds — the production config dropped it to 2,
  which saves tokens but limits retrieval quality
- **VERIFIED SAFE: The `messages` list mutation in `agent.run()` does NOT leak
  back to `_chat_history`**. `telegram_handler.py:133` creates a shallow copy
  via `list(...)`. `agent.py:155-157` appends to this copy (Anthropic SDK
  ContentBlock objects), but since `list()` created a new list container,
  `.append()` only mutates the local copy. The original `_chat_history[chat_id]`
  is untouched. The SDK objects in the local copy are discarded when
  `agent_run()` returns. This is safe but relies on the shallow-copy-is-enough
  invariant — if agent.py ever mutated an existing dict in the list (e.g.,
  `messages[0]["content"] = ...`), it would corrupt chat history
- **No relevance filtering before feeding to Claude**. All 10 Qdrant results go
  into the tool result as JSON. With 512-char chunks, that's ~5KB of context per
  search. Claude has to figure out what's relevant
- **Web search has no domain restrictions**. Claude can search anything. For an
  accounting bot, this is fine, but the system prompt doesn't explicitly
  constrain web search to authoritative sources (e.g., MF RS, PU RS) **# TODO:
  Marko**
- **`system` parameter is a plain string, not a list**. Works, but if you later
  want to add cache_control blocks, you'll need to restructure

**Validate**:

- [ ] Send 5-10 real accounting questions your client would ask. Check: does it
      search documents first? Does it use web search when appropriate? Does it
      cite sources?
- [ ] Test with a question that requires 2 searches (e.g., "compare PDV rates
      between goods and services and tell me the current exchange rate"). With
      max_tool_rounds=2, this may produce an incomplete answer
- [ ] Check token usage per query in logs. Sonnet 4.6 at ~$3/M input and ~$15/M
      output — estimate monthly cost at expected volume

---

## 5. Telegram Handler (`src/telegram_handler.py`)

**What it does**: Telegram polling bot. Message handling, PDF upload
(admin-only), rate limiting, typing indicator, /stats command, weekly digest.

**Explicit decisions**:

- Polling mode (not webhooks) — simpler, no public URL needed
- Per-chat history in memory, max 10 exchanges (20 messages)
- Sliding window rate limit: 10 msgs / 60s per chat
- Admin-only PDF upload with size limit (20MB)
- Markdown→HTML conversion for Telegram display
- Fire-and-forget analytics logging

**Implicit assumptions**:

- **`allowed_chat_ids: []` means allow all**. This is an open bot. Anyone who
  discovers the bot username can use it and burn your API credits. This is the
  single biggest operational risk
- **`admin_chat_ids: []` means no admins**. Nobody can upload PDFs via Telegram
  or run /stats. You need to set this
- **History is in-memory only**. Bot restart = all conversation context lost.
  Users get worse answers on first message after restart (no prior context)
- **`_md_to_html` ordering is correct**: `html.escape` only affects `&`, `<`,
  `>`, `"`, `'` — not `*` or `#`. So the subsequent markdown regex for
  bold/italic/code works. `&` in Claude's response (e.g., "P&L") becomes `&amp;`
  before the markdown regex, which is correct for HTML display (shifting
  positions by +2 chars), the 4096 split lands mid-tag: first message ends with
  `<b>bold te`, second starts with `xt</b>`. Telegram rejects the malformed HTML
  → the `except` fallback sends as plain text → user sees literal `<b>` tags.
  Silent degradation, not a crash
- **Typing indicator runs every 4s in an infinite loop**. If `agent_run()` takes
  30s, that's 7 Telegram API calls just for typing. Fine, but worth knowing
- **`document_handler` saves the PDF to `data/pdfs/` by the original filename**.
  If two admins upload files with the same name, the second overwrites the
  first. No versioning or conflict handling
- **`log_query` is fire-and-forget via `asyncio.create_task()`**. If the bot
  shuts down during this, the log is lost. Acceptable trade-off

**Validate**:

- [ ] **Set `allowed_chat_ids` and `admin_chat_ids` before production**. This is
      non-negotiable **# TODO**
- [ ] Test rate limiting: send 11 messages in <60s, confirm the 11th is rejected
      **# TODO: explain what exectly is implemented in rate limit**
- [ ] Test PDF upload with a real document and verify it appears in Qdrant

do we need to add admin to \*Set `allowed_chat_ids` since they are already in
`admin_chat_ids`

---

## 6. Analytics (`src/analytics.py`)

**What it does**: SQLite with WAL mode. Logs queries, escalations, daily stats.
Provides `/stats [days]` data.

**Explicit decisions**:

- SQLite over PostgreSQL (zero deps, adequate for <300 queries/month)
- WAL mode for async reads without blocking
- Parameterized queries (no SQL injection risk)

**Implicit assumptions**:

- **VERIFIED DEAD CODE (3 items)**: **# TODO: To see what I do with this.**
  1. `log_escalation()` — defined at line 81, never imported or called anywhere.
     Escalation stats will always be 0
  2. `update_daily_stats()` — defined at line 156, never imported or called
     anywhere. The `daily_stats` table is never populated
  3. `ensure_collection()` in `vector_store.py` — defined at line 19, never
     called. Only `recreate_collection()` and `upsert_chunks()` are used
- **`datetime.utcnow()` is deprecated** in Python 3.12+. Currently works but
  emits deprecation warnings. Should be `datetime.now(timezone.utc)` **# TODO:
  can we do this in Belgrade time without warnings, validate with web
  serch/fetch?.**
- **New SQLite connection per write** (`async with aiosqlite.connect(DB_PATH)`).
  SQLite's connection overhead is negligible, so this is fine
- **`now` variable in `get_stats` is computed but never used** (line 100)

**Validate**:

- [ ] Decide if you want escalation tracking. If yes, define what triggers it
      (e.g., Claude says "kontaktirajte mari-mar.rs"). If no, consider removing
      dead code before it confuses future you
- [ ] Decide if you need `daily_stats` aggregation or if `get_stats()`
      live-querying `queries` table is sufficient

---

## 7. Qdrant Keep-alive (`src/qdrant_keepalive.py`)

**What it does**: Pings Qdrant every 6 hours via `get_collections()` to prevent
free-tier hibernation.

**Implicit assumptions**:

- Qdrant Cloud free tier hibernates after inactivity (this is correct for free
  clusters)
- A `get_collections()` call is sufficient to keep it warm
- If the ping fails, it logs a warning but doesn't retry or alert. If Qdrant is
  down for 6+ hours, you won't know until a user query fails
- Same pattern as vector_store: creates a new client per ping

**Validate**:

- [ ] Confirm your Qdrant plan actually hibernates. If you're on a paid plan,
      this is unnecessary overhead

---

## 8. System Prompt (`prompts/system.txt`)

**What it does**: Defines Claude's persona and behavior rules in Serbian.

**Key rules**: be concise, cite sources, don't hallucinate, prefer document
search over web, refer uncertain cases to mari-mar.rs.

**Implicit assumptions**:

- The prompt says "uvek prvo pretražuj dokumenta" (always search documents
  first), but Claude decides tool order autonomously. There's no enforcement —
  Claude may skip document search if the question seems general
- No examples or few-shot demonstrations. Claude is relying on
  instruction-following alone
- No guidance on what NOT to answer (e.g., legal advice disclaimers, scope
  boundaries beyond "kontaktirajte mari-mar.rs") **# TODO: only accountant
  stuff?.**
- The fallback URL (mari-mar.rs) is hardcoded in the prompt. If the client's
  website changes, you need to update this

**Validate**:

- [ ] Test with edge cases: "Da li da tužim svog partnera?" (legal advice
      outside accounting), "Koji je kurs evra danas?" (requires web search, not
      documents) **# TODO: yes.**
- [ ] Verify the bot actually searches documents before web by checking agent
      logs for tool call order **# TODO: yes.**

---

## 9. Infrastructure & Deployment

**Docker**: python:3.12-slim, no Tesseract (removed). Health check on
:8080/health every 30s.

**docker-compose**: single `bot` service, mounts `data/`, `config.yaml`,
`prompts/` as volumes.

**Implicit assumptions**:

- **No restart persistence for Qdrant data**. Qdrant is cloud-hosted, so this is
  fine. But `data/analytics.db` is on a volume mount — if the VPS disk dies,
  analytics are lost. No backup strategy
- **No log aggregation**. Logs go to stdout (Docker default). If you need to
  debug a user complaint from last week, you need `docker logs` with sufficient
  log retention
- **Single container, single process**. No horizontal scaling. If the bot is
  processing a slow Claude request, other users wait (Telegram polling is
  sequential per update). python-telegram-bot does handle concurrent updates via
  asyncio, so this is only a problem if the event loop blocks
- **No secrets management**. `.env` file on disk with plaintext API keys.
  Standard for small deployments, but worth noting
- **Health check only tests HTTP server, not bot functionality**. Qdrant could
  be down, Telegram polling could have stopped, and health check still returns
  "ok"

**Validate**:

- [ ] Test a full restart cycle: `docker compose down && docker compose up -d`.
      Confirm bot starts, health check passes, and queries work within 30s
- [ ] Check Docker log retention: `docker inspect bot | grep LogConfig`

---

## 10. System-Level Concerns

### Cost model

- Claude Sonnet 4.6: ~$3/M input, $15/M output. With 10 chunks (512 chars
  each) + system prompt + history, expect ~3-5K input tokens and ~500-1K output
  tokens per query. At 300 queries/month: **~$5-10/month on Claude**
- Cohere embed-v4.0: free tier is 1000 calls/month. Each query = 1 embed call.
  Ingestion of 200 PDFs with ~50 chunks each = 10K embed calls (batched into
  ~105 API calls). **Free tier may not cover initial ingestion** **# TODO: Think
  about adding paid tier**
- Qdrant Cloud: free tier = 1 cluster, 1GB storage. 200 PDFs _ 50 chunks _ 1024
  dims \* 4 bytes = ~40MB. Fits easily

### Security

- Bot is currently open to all Telegram users (empty allowed_chat_ids) **# TODO:
  yes this has to be addressed.**
- PDF upload accepts any PDF from any admin. A malformed PDF could crash PyMuPDF
  (known CVE history). Low risk but nonzero **# TODO: what can i do to address
  this and fail safly?.**

### Reliability

- No circuit breaker on external APIs. If Cohere is down, every user query fails
  with a generic error **# TODO: can we add better error handling.**
- Rate limiting is per-chat, not global. 100 users each sending 10 msgs/min =
  1000 Claude API calls/min. No global rate limit or cost cap **# TODO: not for
  now limit by limiting usefulness to users.**
- **VERIFIED BUG: orphaned user message on error**. If `agent_run()` raises
  (telegram_handler.py:134), the user message is already appended at line 122,
  but the assistant response (line 136) is skipped. The `except` block
  (line 166) sends an error message to the user but does NOT remove the orphaned
  message from `_chat_history`. On next query, history has two consecutive
  `{"role": "user"}` messages with no assistant response between them. Claude's
  API accepts this but it may produce confused responses

---

## Next Steps (Prioritized)

### Must-do before production

1. **Set `allowed_chat_ids` and `admin_chat_ids`** in config.yaml. Without this,
   anyone can use the bot and burn your API budget
2. **Test with real PDF corpus** — ingest all ~200 PDFs, run 10-20
   representative queries, assess retrieval quality
3. **Decide on `max_tool_rounds`** — 2 is very restrictive. Try 3-4 and compare
   answer quality vs cost

### Should-do soon

4. **Add relevance score threshold** to vector_store.search() — filter out
   results below e.g., 0.5 to stop feeding Claude noise **# TODO: do conisder
   this aposlutly.**
5. **Add filename-based deletion** to vector_store — needed for PDF replacement
   workflow ("admin uploads corrected version of same document")
6. **Wire up escalation tracking** or remove the dead code — right now
   `log_escalation()` and the `escalations` table are unused
7. **Singleton Qdrant client** — stop creating a new HTTPS connection per query

### Nice-to-have

8. **Add a cost cap** — track daily token spend in analytics.db, stop responding
   after threshold with "Dnevni limit je dostignut"
9. **Add health check depth** — ping Qdrant and verify collection exists in
   health endpoint, not just return "ok"
10. **Chunk size tuning** — experiment with 768 or 1024 chars. Serbian
    accounting text with legal references may benefit from larger chunks.
    Measure retrieval quality before/after
11. **CLAUDE.md is stale** — references "search_documents currently commented
    out" and "OCR fallback via Tesseract", both outdated. Update after
    stabilizing

# TODO: Optimize chat history (if conversation is not from today clean chat history for user, have a rolling window)
