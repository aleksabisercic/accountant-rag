# Pre-Meeting Prerequisites: Hosting Setup

**Why this matters:** We need a cloud server to run the bot. To avoid a painful
handoff later (migration, card swap, support tickets), **the hosting account
should be yours from day one.** I get invited as a collaborator, do the setup,
and at project end I remove myself. No data moves, no downtime, no billing
handover.

**Your total time:** ~15 minutes, plus 0–24h waiting on Hetzner's identity
verification.

---

## What you need to do before our next meeting

### 1. Create a Hetzner Cloud account

- Go to https://console.hetzner.cloud
- Sign up/Register with your company email
- Use a credit or debit card you're comfortable leaving on file long-term (this
  card pays ~€4/month for the server)

### 2. Complete identity verification

- Hetzner requires ID verification for all new accounts (EU compliance)
- They may ask for: a government ID photo, or a small authorization charge on
  the card
- **This can take anywhere from a few minutes to 24 hours** — start early so it
  doesn't block our meeting

### 3. Create a Project

- In the Hetzner Cloud Console, click **+ New Project**
- Name it: `accountant-rag` (or whatever you prefer — just tell me the name)

### 4. Invite me as a member

- Open the project → **Sharing** (or **Members**, depending on console version)
- Enter my email: **<your-email-here>** ← _Aleksa: fill this in before sending_
- Grant full access (admin-level)
- I'll get an email invite and accept it

### 5. Confirm before the meeting

Reply to me with:

- [ ] Account created and verified
- [ ] Project created (name: **\_\_\_**)
- [ ] Invite sent to my email

---

## What I will do after you've done the above

- Provision a server (Hetzner CX23: 2 vCPU, 4GB RAM, 40GB disk — ~€4–5/month, located in Falkenstein, Germany)
- Install Docker and deploy the bot
- Run the initial document ingestion
- Verify the bot is live on Telegram
- Hand you a short ops runbook (how to restart, check logs, see usage)

## What this costs you ongoing

| Item                                     | Cost                        |
| ---------------------------------------- | --------------------------- |
| Hetzner CX23 server                      | ~€4–5/month                 |
| Anthropic, Cohere, Qdrant, Telegram APIs | Your existing accounts/keys |
| **Total new infra cost**                 | **~€4/month**               |

## At project offboarding

- You revoke my access in one click
- Server, data, and billing all stay with you untouched
- No migration, no card changes, no support tickets

---

## Questions you may have

**Q: Why not use AWS / Google Cloud / Azure?** Overkill and 3–5× the price for
this workload. The bot needs one small Linux box, not a platform.

**Q: Why Hetzner specifically?** Cheapest solid EU provider, and their German
data centers are closest to the vector database we're already using, which means
faster responses.

**Q: Can I use an existing cloud account I already have?** Yes — if you already
have DigitalOcean, AWS, or similar, tell me which and I'll adapt. Hetzner is the
recommendation, not a requirement.

**Q: What if I don't want to put my card on Hetzner?** Alternative: I can host
it under my account and we migrate at the end. It works, but it's slower at
handoff and introduces a brief downtime window. Your call — flag it before the
meeting if you prefer this.

---

## Appendix — Plan rada (interno, SR)

Šta ću konkretno uraditi nakon što klijent završi preduslove iznad. Ovo je
moja lista, ne ide u klijentsku verziju dokumenta.

### 1. Pristup i priprema (~5 min)

- Prihvatim poziv u Hetzner projekat preko mejla
- Napravim API token: Project → Security → API Tokens → read/write
  (token se prikazuje samo jednom — sačuvati odmah u password manager)
- Dodam svoj SSH ključ u projekat:
  ```bash
  hcloud context create accountant-rag   # paste token
  hcloud ssh-key create --name aleksa-laptop \
    --public-key-from-file ~/.ssh/id_ed25519.pub
  ```

### 2. Provizioniranje servera (~2 min)

```bash
hcloud server create \
  --name accountant-rag \
  --type cx23 \
  --image ubuntu-24.04 \
  --location fsn1 \
  --ssh-key aleksa-laptop
```

- `cx23` = 2 vCPU, 4GB RAM, 40GB disk (~€4–5/mesec)
- `fsn1` = Falkenstein (Nemačka), najbliže Qdrant klasteru u `europe-west3`
- Zapisati IP adresu iz outputa

### 3. Osnovna zaštita servera (~5 min)

Pre bilo kakvog deploy-a:

- SSH na server: `ssh root@<ip>`
- Update sistema: `apt update && apt upgrade -y`
- Napravim non-root usera za deploy (ne radimo sve kao root)
- UFW firewall: dozvoliti samo 22 (SSH) i 8080 (health check, interno);
  sve ostalo blokirati
- Disable password SSH login (samo key-based): `/etc/ssh/sshd_config` →
  `PasswordAuthentication no`
- Instalirati `fail2ban` protiv brute force SSH napada

### 4. Docker i deploy (~10 min)

```bash
# Instaliraj Docker
curl -fsSL https://get.docker.com | sh

# Kloniraj repo (pre toga: git push sa mog laptopa)
git clone <repo-url> && cd accountant-rag

# .env sa rotiranim ključevima (kreiram ručno, nikad ne commit-ujem)
nano .env

# Pripremi folder za PDF dokumente
mkdir -p data/pdfs

# Prebaci PDF-ove sa laptopa
# (sa lokalne mašine u drugom terminalu:)
# scp data/pdfs/*.pdf root@<ip>:/root/accountant-rag/data/pdfs/

# Pokreni ingestion — DESTRUKTIVNO, briše i pravi Qdrant kolekciju
docker compose run --rm bot python ingest.py

# Pokreni bota
docker compose up -d

# Provera
curl http://localhost:8080/health       # treba da vrati "ok"
docker compose logs -f                  # pratiti prvih par minuta
```

### 5. Verifikacija end-to-end (~5 min)

- Otvoriti Telegram, poslati test upit botu
- Probati upload PDF-a (ako podržava)
- Probati admin komande
- Proveriti da analytics SQLite pravi zapise
- Proveriti da nema errora u `docker compose logs`

### 6. Runbook za klijenta (~15 min)

Kratak MD fajl sa osnovnim ops komandama koje klijent može sam da pokrene:

- Kako pogledati logove: `docker compose logs -f`
- Kako restartovati bota: `docker compose restart bot`
- Kako pogledati status: `docker compose ps`
- Kako ažurirati nakon git pull-a: `docker compose up -d --build`
- Kako prebaciti backup SQLite baze (za analytics)
- Koga kontaktirati ako bot padne (mene dok traje podrška)

### 7. Rizici koje treba reći klijentu na meetingu

- Chat history se trenutno drži u memoriji — izgubi se na restart
- Nema retry logike za Cohere/Qdrant outage — zahtevi padaju tiho
- `ingest.py` je destruktivan — svako pokretanje briše vektore
- SQLite analytics baza nije backup-ovana — gubi se ako disk krahira
- Health endpoint vraća samo "ok", ne proverava da li su Qdrant/Cohere zaista
  dostupni

Ovo su poznati trade-offi. Ako klijent hoće da ih pokrijemo, to je poseban
scope.

### 8. Offboarding checklist (za kraj projekta)

- Obrisati svoj API token iz Hetzner projekta
- Ukloniti svoj SSH ključ sa servera
- Ukloniti sebe kao člana projekta
- Predati klijentu: runbook, listu API ključeva (gde da ih rotira), kontakt
  za hitne slučajeve ako postoji period podrške

