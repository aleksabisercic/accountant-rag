# Pre-Meeting Prerequisites: Cohere API Key (Production)

**Why this matters:** The bot uses Cohere's Rerank model to reorder vector search results before sending them to Claude — this is what makes retrieval quality acceptable. Cohere's **Trial keys cannot be used commercially** (per their terms) and cap rerank at 10 requests/minute, which will cause the bot to fail under normal load. A Production (paid) key is required.

**Your total time:** ~10 min. No waiting period.

---

## What you need to do before our next meeting

### 1. Create a Cohere account
- Go to https://dashboard.cohere.com
- Sign up with your company email

### 2. Upgrade to a Production API key
- Dashboard → **API Keys** → **Create Production Key**
- This requires adding a credit card (Dashboard → **Billing** → **Payment methods**)
- Production keys are pay-as-you-go — you only pay for actual usage

### 3. Set a monthly spending limit
- Dashboard → **Billing → Spend limits**
- Recommended starting cap: **$20/month**
- Enables hard stop if something goes wrong

### 4. Generate the Production API key
- Dashboard → API Keys → **+ Create Production Key**
- Name: `accountant-rag-prod`
- Copy the key **immediately** — shown only once

### 5. Share the key with me securely
- **Do not** send via email, Slack, or WhatsApp
- Use **Bitwarden Send** (https://send.bitwarden.com — free, no account, 24h expiry + 1 access)
- Or 1Password shared item
- Send me the URL via any channel — it's useless without the one-time access

### 6. Confirm before the meeting
Reply with:
- [ ] Cohere account created
- [ ] Production key active (not Trial)
- [ ] Spending limit set ($___/month)
- [ ] Key shared with me

---

## What I will do after you've done the above

- Add the key to `.env` on the server
- Run a test query and verify the reranker shows up in the bot logs
- Confirm usage appears within ~1 minute in the Cohere dashboard
- Pin `rerank-v3.5` (multilingual, 4k context) in bot config unless we decide otherwise

## Cost

Cohere Rerank production pricing is pay-as-you-go per "search unit" (1 query with up to 100 documents = 1 search unit). Pricing is not listed publicly on a simple page — verify current rate in the Cohere dashboard before keying in. Historical rate for Rerank v3.5 has been **~$2.00 per 1,000 search units**.

Realistic monthly cost for a small bot (~100 queries/day = ~3,000 search units/month):
**~$5–10/month.** Spending cap protects you if usage spikes.

## At project offboarding

- Dashboard → API Keys → **revoke** `accountant-rag-prod` (cuts my access instantly)
- Optionally create a fresh Production key for yourself
- Account, billing, and usage history stay with you untouched

---

## FAQ

**Q: Can't we use the free Trial tier to save money?**
No. Cohere's Trial terms prohibit commercial use, and the 10 req/min rerank cap will cause the bot to drop queries under normal bursts. Production key is required.

**Q: Can I use an existing Cohere account I already have?**
Yes — just make sure it's on Production (not Trial) and that the key is named clearly so I don't confuse it with any other project. Tell me which account email you used.

**Q: What if the $20/month cap is hit?**
Cohere API returns 402 errors, the bot gracefully degrades (retrieval still works, reranking is skipped) until you raise the cap or the month resets. Quality drops but the bot keeps answering.
