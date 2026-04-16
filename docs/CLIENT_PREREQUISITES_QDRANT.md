# Pre-Meeting Prerequisites: Qdrant (Vector Database)

**Why this matters:** The bot stores PDF embeddings in Qdrant and queries them on every user question. The current bot already has a working Qdrant Cloud cluster from development. You have two paths — **pick one before the meeting**.

---

## Decision: Path A or Path B

### Path A — Keep the existing cluster, migrate at offboarding
- **You do nothing now.** Cluster, data, and key stay as they are.
- At offboarding I migrate the cluster's contents (or give you credentials to claim it) into an account you own.
- **Pros:** Zero setup work today. Bot keeps working through handoff.
- **Cons:** Migration at the end introduces a brief downtime window (~10 min) and a re-ingest. Same pattern we're avoiding on Hetzner.

### Path B — Create your own Qdrant Cloud account now (recommended)
- You own the cluster from day one, same logic as Hetzner and Anthropic.
- I re-run the ingestion against your cluster on first deploy (~20 min, safe — empty cluster, nothing to lose).
- **Pros:** Clean handoff, no downtime at the end, you control billing from day one.
- **Cons:** ~15 min of setup on your side before the meeting.

**My recommendation: Path B.** The setup is cheaper than the migration.

---

## If you pick Path B — what to do before our next meeting

**Your total time:** ~15 min. No waiting period.

### 1. Create a Qdrant Cloud account
- Go to https://cloud.qdrant.io
- Sign up with your company email

### 2. Create a cluster
- Dashboard → **Clusters** → **+ Create**
- **Provider:** GCP
- **Region:** `europe-west3` (Frankfurt) — lowest latency to the Hetzner server in Falkenstein
- **Tier:** Start on **Free (1GB)** — sufficient for a few thousand PDF pages. Upgrade later if you outgrow it.
- Name: `accountant-rag`

### 3. Generate an API key
- Cluster detail page → **Data Access Control** → **Create API Key**
- Scope: this cluster only
- Name: `accountant-rag-prod`
- Copy immediately — shown once

### 4. Copy the cluster URL
- Cluster detail page → **Endpoint URL** (looks like `https://xxxxxxxx.europe-west3-0.gcp.cloud.qdrant.io:6333`)

### 5. Share both with me securely
- **Do not** send via email, Slack, or WhatsApp
- Use **Bitwarden Send** (https://send.bitwarden.com — free, 24h expiry + 1 access)
- Send the URL over any channel

### 6. Confirm before the meeting
Reply with:
- [ ] Account created
- [ ] Cluster `accountant-rag` running in `europe-west3`
- [ ] API key + cluster URL shared with me

---

## What I will do after you've done the above (Path B)

- Point `.env` on the server at your cluster
- Copy PDF documents to the server
- Run `python ingest.py` — embeds and writes to your cluster (destructive, but cluster is empty so it's safe)
- Verify with a test query that retrieval returns correct chunks

## Cost

- **Free tier:** 1GB cluster, €0/month. Enough for roughly 300k text chunks — likely plenty for your document set.
- **Paid tier:** starts around **~€25/month** for 4GB if you outgrow the free tier (large document set or high-dimensional embeddings).

We'll start on free. I'll tell you during deploy if the data won't fit.

## At project offboarding

- **Path A:** we execute the migration (see Path A above), then you revoke the old credentials
- **Path B:** cluster is already yours — rotate the API key and you're done

---

## FAQ

**Q: Why `europe-west3` specifically?**
It's the GCP region closest to Falkenstein (~300km / ~5ms latency). Every query hits Qdrant, so latency matters more here than for the LLM APIs.

**Q: Can I self-host Qdrant on the Hetzner box instead?**
Technically yes, but it adds operational burden (backups, upgrades, disk sizing) that's not worth it at this scale. Qdrant Cloud free tier is simpler.

**Q: What if my PDFs generate more than 1GB of vectors?**
I'll catch it during ingest (the script will error out on quota). We'd then upgrade to the paid tier or switch to smaller embeddings.
