# Accountant RAG Bot

A Serbian-language accounting assistant on Telegram. It answers tax and
bookkeeping questions over a curated PDF corpus using an agentic Claude loop,
and flags its own uncertainty instead of guessing.

Currently running in production for a Serbian accounting firm.

## Context

Serbian accountants get the same regulatory questions over and over, and the
source material — tax codes, bylaws — is dense, Cyrillic-heavy, and changes
often. A generic chatbot tends to answer confidently even when it's wrong, which
is worst exactly where the stakes are highest. The design choice here is a
narrow, grounded assistant that stays close to its corpus and hands off when it
isn't sure.

## Notable design choices

- **Confidence-gated answers.** Each response carries a confidence level
  (`HIGH/MED/LOW/NONE`). Low-confidence ones are escalated to a human admin with
  the question and draft reply, so the bot's weak spots become a review queue
  rather than silent errors.
- **Built for the language.** Cyrillic↔Latin transliteration at both index and
  query time, plus multilingual embeddings, so a query in either script matches
  the same content.
- **Agentic, not single-shot.** Claude chooses when to search the corpus, search
  the web, or run sandboxed code, within a bounded tool-use loop — corpus
  retrieval for "what's the rule," web search for "what changed this year."
- **No app to learn.** It lives entirely in Telegram, where the users already
  are — no install, no new login, no separate dashboard. The whole experience is
  a chat.
- **Onboarding non-technical users can actually do.** A one-tap invite link, or
  a guided in-chat sign-up with admin approve/deny. The firm manages its own
  access; the developer isn't in the loop.
- **Self-serve content updates.** Admins upload new PDFs through Telegram; the
  bot extracts, chunks, embeds, and replaces the old version without a redeploy.
- **Lean footprint.** Runs on a 2GB VPS — no GPU, no heavy ML dependencies,
  managed vector DB.

## Features

| Capability     | Detail                                                                       |
| -------------- | ---------------------------------------------------------------------------- |
| Q&A            | Grounded answers with confidence labels + standardized disclaimer            |
| Access control | Invite links, self-registration with approval, admin allow-list              |
| Content ops    | In-chat PDF upload → extract → chunk → embed → replace                       |
| Admin insight  | `/stats`, `/pitanja` question log, weekly digest, escalation alerts          |
| Reliability    | Deep health check (Qdrant + Cohere probes), uptime monitoring, daily backups |

## How it works

```text
PDF corpus ──ingest──▶ chunk + transliterate ──▶ Cohere embed-v4.0 ──▶ Qdrant
                                                                          │
Telegram ──▶ Agent loop (Claude Sonnet 5) ────┬─ search_documents ────────┘
                                              ├─ web_search (native)
                                              └─ code execution (sandboxed)
```

## Tech stack

Anthropic SDK (raw, no agent framework) · Cohere embed-v4.0 · Qdrant ·
PyMuPDF4LLM · python-telegram-bot · Docker · Hetzner

## Run it

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # ANTHROPIC / COHERE / QDRANT / TELEGRAM keys
cp config.example.yaml config.yaml

python ingest.py                # Index PDFs from data/pdfs/
python bot.py                   # Run the bot (polling)
.venv/bin/pytest tests/ -v      # Unit tests
```

Or `docker-compose up -d --build`.
