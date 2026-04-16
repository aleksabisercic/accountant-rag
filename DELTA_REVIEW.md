# Accountant RAG — Delta Review (vs PROJECT_REVIEW.md)

Baseline: `PROJECT_REVIEW.md`. This covers what changed, what's new, and what still needs attention.

---

## System-Level Delta

**Architecture unchanged**: Claude Sonnet 4.6 agentic loop, Cohere embed-v4.0, Qdrant, Telegram polling. What changed is maturity — several gaps from the original review are now closed.

| Area | Before | Now | Status |
|------|--------|-----|--------|
| Chunk size | 512 chars / 50 overlap | 2000 chars / 0 overlap | Fixed |
| Tools | 2 (search + web) | 3 (+calculator) | New |
| max_tool_rounds | 2 | 5 | Fixed |
| Score filtering | None | min_score 0.35 | Fixed |
| Qdrant client | New per call | Singleton | Fixed |
| ACL | Config arrays, open by default | JSON-backed, persistent, runtime-mutable | New |
| Filename deletion | None | delete_by_filename() | Fixed |
| Document listing | None | /dokumenti command | New |
| User management | None | /adduser, /removeuser, /addadmin, /removeadmin | New |
| Broadcast | None | /broadcast | New |
| HTML splitting | Potentially broken at tag boundary | Tag-aware _split_html | Fixed |
| Embed retry | None | Exponential backoff (5 retries) | Fixed |
| Scope boundary | None beyond mari-mar.rs | Explicit topic restriction in prompt | Fixed |
| Contextual embeddings | N/A | Implemented, disabled by default | New |
| Table extraction fix | N/A | _fix_misclassified_tables() | New |

**Bottom line**: Most critical items from the original review are addressed. What remains is edge-case hardening and operational maturity.

---

## 1. PDF Ingestion — Delta

**Files**: `src/pdf_loader.py`, `ingest.py`, `review_pdf_output.py`

**What changed**:
- Chunk size 512→2000, overlap 50→0, separators now Serbian-aware (`. `, `; `)
- New `process_single_pdf_with_doc()` returns `(full_doc_text, chunks)` for contextualizer
- `_fix_misclassified_tables()`: regex detects PyMuPDF4LLM's "picture intentionally omitted" blocks and replaces with actual table markdown via `pymupdf.find_tables()`. Positional mapping (i-th picture → i-th table)
- Chunk metadata now includes `embedding_text` (source+section prepended), `parent_text` (full pre-split section)
- `review_pdf_output.py`: QA tool that dumps markdown + chunks to `data/review/` per PDF

**Still needs attention**:
- **Double transliteration**: `process_single_pdf_with_doc` transliterates, then `chunk_markdown()` transliterates again internally. Idempotent but wasteful
- **Dead code**: `if not md.strip(): return "", []` in `process_single_pdf_with_doc` is unreachable (same pattern as before — `extract_markdown()` raises before returning empty)
- **`load_all_pdfs()` is orphaned** for the contextual path. `ingest.py` now loops per-document. Two ingestion code paths doing similar things — divergence risk
- **Table-picture mapping is positional**, not page-correlated. If ordering doesn't match (picture block on page 5 maps to table on page 2), wrong content gets injected. Fine for simple PDFs, fragile for complex ones
- **parent_text storage multiplier**: stored in every child chunk. A 10-page section with 20 children = same text stored 20×. Inflates Qdrant payload size

---

## 2. Embeddings — Delta

**File**: `src/embeddings.py`

**What changed**:
- **Retry logic added**: exponential backoff, 5 retries on `TooManyRequestsError`, 10s base delay doubling per attempt

**Still needs attention**:
- Module-level client instantiation unchanged — bad API key fails silently at import, loudly at first call
- Only retries `TooManyRequestsError`. Network errors, timeouts not covered

---

## 3. Vector Store — Delta

**File**: `src/vector_store.py`

**What changed**:
- **Singleton client** — persistent `AsyncQdrantClient` reused across calls (was: new client per call)
- **min_score filtering** — results below threshold (0.35) filtered before reaching Claude
- **`delete_by_filename()`** — removes all points matching a filename via FilterSelector. Used by Telegram upload before upsert
- **`list_documents()`** — scrolls all points, returns `{filename: chunk_count}`. Powers `/dokumenti`
- **`filename` KEYWORD payload index** created by both `ensure_collection()` and `recreate_collection()`

**Still needs attention**:
- **min_score 0.35 calibrated on tiny corpus** (12 chunks, 2 PDFs per CLAUDE.md). Score distributions will shift with 200+ docs — needs re-evaluation after real ingestion
- **No atomicity on upsert**: partial failure leaves partial document in Qdrant. Re-upload via Telegram now handles this (delete-then-upsert), but a crash between delete and upsert leaves the document gone

---

## 4. Agent — Delta

**File**: `src/agent.py`

**What changed**:
- **max_tool_rounds: 5** (was 2). Claude can now do multiple search + reformulate cycles
- **Calculator tool**: numexpr-based, accepts array of expressions, safety-capped (10 expressions, 200 chars each). Math functions (sqrt, log, trig), constants (pi, e). Accounting scenarios (PDV calc, salary, compound interest)
- Source tracking with `Source` dataclass: type, name, section, url, cited_text, score. Deduplication via `seen_sources` set

**Still needs attention**:
- The shallow-copy invariant for chat history still applies. Safe currently, fragile if agent.py ever mutates existing dicts in the message list
- System prompt is still a plain string, not a list. Blocks cache_control if you want prompt caching later

---

## 5. Telegram Handler — Delta

**File**: `src/telegram_handler.py`

**What changed** (major):
- **Full ACL system** (`src/acl.py`): JSON-backed at `data/acl.json`, seeds from config on first run, runtime-mutable. Admins implicitly allowed. Atomic writes (temp→rename)
- **New commands**: `/adduser <ID> [name]`, `/removeuser <ID>`, `/users`, `/addadmin <ID> [name]`, `/removeadmin <ID>`, `/dokumenti`, `/broadcast <msg>`, `/help`, `/clear`
- **`_split_html`**: tag-boundary-aware splitting at 4096 chars. Tracks open/close tags, injects closing/reopening tags at split points. No more malformed HTML
- **PDF upload flow**: download → extract → (optional contextualize) → embed → **delete old vectors by filename** → upsert. Replacement, not duplication
- **Broadcast**: admin-only, sends to all users+admins (deduplicated), rate-limited (50ms sleep), handles `Forbidden` (blocked bot)

**Still needs attention**:
- **Orphaned message bug: STILL PRESENT**. User message appended at ~line 548; if `agent_run()` throws, no cleanup. Next query has two consecutive user messages with no assistant response
- **No typing indicator during PDF upload**. Message handler has `keep_typing()`, document handler doesn't. Large PDFs = silent wait after "⏳"
- **Filename collision on upload**: uploading different `report.pdf` files overwrites on disk. Qdrant is fine (delete-then-upsert), but the old file is lost from `data/pdfs/`
- **Path traversal**: filename comes from Telegram metadata. Telegram likely sanitizes, but `PDF_DIR / doc.file_name` with `../` in filename resolves outside `data/pdfs/`. Trust in external system
- **ScannedPDFError leaves file on disk**: download completes, extraction fails, file stays in `data/pdfs/`. Accumulates orphans

---

## 6. Contextual Embeddings — Delta (NEW)

**Files**: `src/contextualizer.py`, config section `contextual_embeddings`

**What it does**: Before embedding, sends each chunk + full document to Haiku, which generates 2-3 sentence context prefix. Embedded text = context + chunk. Stored text = raw chunk. Query path unchanged.

**Disabled by default** in config.yaml. Reviewed in detail in `CONTEXTUAL_EMBEDDINGS_REVIEW.md`.

**Key concerns if enabled**:
- No retry logic (unlike agent.py). Rate limit during ingestion → silent fallback to raw text, mixed-quality index
- Caching requires 4096+ token documents. Small docs pay full price silently
- No persistence of generated context — can't debug retrieval quality
- Adds 30-60s to Telegram PDF upload with no progress feedback
- LLM context can hallucinate, permanently embedded

---

## 7. Analytics — No Delta

**File**: `src/analytics.py`

**Unchanged**:
- `log_escalation()` and `update_daily_stats()` still dead code
- `datetime.utcnow()` still used (deprecated Python 3.12+)
- `now` variable in `get_stats` still computed but unused

---

## 8. Qdrant Keep-alive — No Delta

**File**: `src/qdrant_keepalive.py`

Unchanged. Still creates new client per ping (separate from the singleton in vector_store — intentional, avoids stale connection issues on 6-hour intervals).

---

## 9. System Prompt — Delta

**File**: `prompts/system.txt`

**What changed**:
- **Explicit scope boundary**: restricted to accounting, taxes, finance, business regulation. Out-of-scope → "specijalizovan sam samo za ove teme"
- **Calculator instruction**: "use calculator for all math" — prevents Claude from doing mental arithmetic (error-prone with Serbian number formatting)
- **No Markdown tables**: explicit instruction because Telegram renders them poorly

---

## 10. Tests — Delta

**New test files**:
- `tests/test_acl.py`: ACL persistence, seed-from-config, add/remove, implicit admin auth
- `tests/test_vector_store.py`: delete_by_filename, list_documents, replacement flow
- `tests/test_calculator.py`: arithmetic, math functions, batch, safety caps, accounting scenarios
- Existing: `test_html_splitting.py`, `test_chunking.py`

**Coverage gaps** (unchanged):
- No tests for: agent loop, embeddings (API-dependent), Qdrant search/scoring, Telegram message handling, PDF extraction, analytics
- `test_local.py` covers some of these manually but requires API keys

---

## 11. Infrastructure — Minor Delta

- Docker and compose configs unchanged
- Health check still shallow (HTTP 200 only, doesn't probe Qdrant or Telegram)

---

## Known Bugs (Current)

| Bug | Severity | Location | Status |
|-----|----------|----------|--------|
| Orphaned user message on error | Medium | telegram_handler.py ~line 548 | **Still open** |
| `datetime.utcnow()` deprecated | Low | analytics.py | **Still open** |
| Dead code: `log_escalation()`, `update_daily_stats()` | Low | analytics.py | **Still open — decide: wire up or remove** |
| Dead code: unreachable empty-check in `process_single_pdf_with_doc` | Trivial | pdf_loader.py | **Still open** |
| `load_all_pdfs()` orphaned from contextual path | Low | pdf_loader.py | **Still open** |

---

## Recommended Next Steps

### Before production

1. **Fix orphaned message bug** — pop user message from history on `agent_run()` exception. 5-line fix, prevents confused responses after errors
2. **Re-calibrate min_score** after ingesting full corpus. 0.35 was tuned on 2 PDFs. Run 20 representative queries, inspect score distributions
3. **Verify admin_chat_ids is set** in production config (currently has one ID — confirm it's correct)

### Should-do soon

4. **Add typing indicator to document_handler** — copy `keep_typing()` pattern. Trivial, noticeable UX
5. **Sanitize upload filenames** — strip path components: `Path(doc.file_name).name` instead of raw `doc.file_name`. Prevents path traversal
6. **Add retry to contextualizer** if you plan to enable contextual embeddings. Without it, rate limits silently degrade index quality
7. **Clean dead code** — remove `log_escalation()`, `update_daily_stats()`, unreachable empty-checks. Or wire them up. Decide, don't leave ambiguous

### Worth considering

8. **Store contextualized_text in Qdrant payload** — zero extra cost (not indexed), gives debug visibility
9. **Deep health check** — probe Qdrant collection existence + Cohere embed in `/health`. Current check is meaningless
10. **Persist chat history** — SQLite alongside analytics. Restart = context lost. For a client-facing bot, this matters
11. **Pass filename to contextualizer prompt** — currently LLM infers document name from content. Explicit is better
12. **Migrate `datetime.utcnow()`** → `datetime.now(timezone.utc)` — trivial, removes deprecation warnings
