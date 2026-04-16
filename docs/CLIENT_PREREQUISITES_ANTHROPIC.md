# Pre-Meeting Prerequisites: Anthropic (Claude) API Key

**Why this matters:** The bot uses Claude to generate answers from retrieved
context. This is the **biggest ongoing cost** — budget guardrails up front are
non-negotiable. Same handoff logic as Hetzner: you own the account from day one,
I get a scoped API key, at project end you revoke it in one click.

**Your total time:** ~10 min. No waiting period.

---

## What you need to do before our next meeting

### 1. Create an Anthropic account

- Go to https://console.anthropic.com
- Sign up with your company email

### 2. Add payment method

- Console → **Settings → Billing → Payment methods** → add card
- Purchase initial credits (minimum $5) or enable auto-recharge

### 3. Set a monthly spending limit

- Console → **Settings → Billing → Usage limits**
- Recommended starting cap: **$30/month**
- Hard cap: API stops working when hit. Soft cap: you get an email alert. Set
  both.

### 4. Create a dedicated Workspace

- Console → **Settings → Workspaces → Create workspace**
- Name: `accountant-rag`
- Purpose: isolates this project's spend and key from anything else you may
  build on Anthropic later

### 5. Generate an API key under that workspace

- Console → **Settings → API keys → Create key**
- Workspace: `accountant-rag`
- Name: `accountant-rag-prod`
- Copy the key **immediately** — shown only once

### 6. Share the key with me securely

- **Do not** send via email, Slack, or WhatsApp
- Use **Bitwarden Send** (https://send.bitwarden.com — free, no account needed,
  set 24h expiry + 1 access)
- Or 1Password shared item if you already use it
- Send me the Bitwarden Send URL via any channel — the URL alone is useless
  without the one-time access

### 7. Confirm before the meeting

Reply with:

- [ ] Account created
- [ ] Spending limit set ($\_\_\_/month)
- [ ] Workspace `accountant-rag` created
- [ ] API key generated and shared

---

## What I will do after you've done the above

- Add the key to `.env` on the server
- Run a test query end-to-end and verify Claude response appears in Telegram
- Confirm the workspace sees usage within ~1 minute in the Anthropic console
- Pick the right model for your cost/quality target (see below) and pin it in
  config

## Model & cost

Pricing as of 2026-04-15 (verify current at https://claude.com/pricing):

| Model      | Input ($/M tokens) | Output ($/M tokens) | When to pick                                |
| ---------- | ------------------ | ------------------- | ------------------------------------------- |
| Opus 4.6   | $5                 | $25                 | Complex reasoning, high accuracy needed     |
| Sonnet 4.6 | $3                 | $15                 | **Recommended default** — best quality/cost |
| Haiku 4.5  | $1                 | $5                  | Highest volume, simpler questions           |

Realistic monthly cost for a small-to-medium bot (~100 queries/day, Sonnet 4.6,
~2k input + 500 output per query): **~$15–25/month.** Heavy use or Opus can 3–5×
this. Spending cap protects you either way.

Prompt caching (supported on all models) can cut input cost by ~90% on the
repeated system prompt — I'll enable this in the bot config.

## At project offboarding

- Console → API keys → **revoke** `accountant-rag-prod` (cuts my access
  instantly)
- Optionally create a fresh key for yourself
- Account, workspace, billing, and usage history all stay with you untouched

---

## FAQ

**Q: Why a dedicated workspace instead of just using the default?** Cost
attribution and kill-switch. If anything goes wrong (runaway loop, leaked key),
you can suspend this workspace without touching any other work you have on
Anthropic.

**Q: Can I use an existing Claude/Anthropic account I already have?** Yes — in
that case, just create the workspace + key under it and skip step 1–2. Tell me
which account email you used so I know where to look at usage.

**Q: What if I hit the $30/month cap unexpectedly?** The bot stops answering
until you raise the cap or the month resets. This is the safe failure mode — no
surprise bills. First week of real usage tells us if $30 is too low or too high.
