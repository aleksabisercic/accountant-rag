#!/usr/bin/env bash
# Push code update to mari-mar-bot Hetzner server.
# Prerequisite: commits already pushed to origin/main on GitHub.
#
# Usage:
#   ./scripts/deploy.sh                    # deploy to default server
#   MARI_MAR_BOT_SERVER=user@host ./scripts/deploy.sh  # override target
#
# What it does:
#   1. SSH to server, git fetch, show pending commits, git pull --ff-only
#   2. docker compose up -d --build (rebuilds only changed layers)
#   3. Wait, show container status + recent error/startup logs
#
# Rollback: ssh deploy@server "cd /opt/mari-mar-bot && git checkout <prev-sha> && docker compose up -d --build"

set -euo pipefail

SERVER="${MARI_MAR_BOT_SERVER:-deploy@178.105.109.233}"
REPO_PATH="/opt/mari-mar-bot"

echo "==> Pre-flight: confirm origin/main is pushed"
LOCAL_HEAD=$(git rev-parse HEAD)
REMOTE_HEAD=$(git ls-remote origin refs/heads/main | cut -f1)
if [ "$LOCAL_HEAD" != "$REMOTE_HEAD" ]; then
  echo "  ✗ Local HEAD ($LOCAL_HEAD) != origin/main ($REMOTE_HEAD)"
  echo "  Push your commits first: git push origin main"
  exit 1
fi
echo "  ✓ Local HEAD matches origin/main"

echo "==> Pulling on $SERVER:$REPO_PATH"
ssh "$SERVER" "cd $REPO_PATH && \
  git fetch origin --quiet && \
  echo '  Server currently at:' && \
  git --no-pager log --oneline -1 && \
  PENDING=\$(git log HEAD..origin/main --oneline) && \
  if [ -z \"\$PENDING\" ]; then echo '  Already up to date.'; else echo '  Pending:' && echo \"\$PENDING\" | sed 's/^/    /'; fi && \
  git pull --ff-only origin main"

echo "==> Rebuilding container"
ssh "$SERVER" "cd $REPO_PATH && docker compose up -d --build 2>&1 | tail -3"

echo "==> Health check (waiting 6s for startup)"
sleep 6
ssh "$SERVER" "cd $REPO_PATH && \
  docker compose ps --format 'table {{.Name}}\t{{.Status}}' && \
  echo '--- recent logs ---' && \
  docker compose logs --tail=30 bot 2>&1 | grep -E 'ERROR|Application started|Traceback' | tail -10 || true"

echo "==> Health endpoint"
# -m 12: a cold-container deep probe is sequential Qdrant(≤3s)+Cohere(≤3s) ⇒ up to ~6s,
# plus TLS/wake on a hibernated Qdrant free tier — 5s would false-alarm on a healthy deploy.
# -f: make curl fail (non-zero) on HTTP 503 so a degraded-dependency deploy surfaces here.
curl -fsS -m 12 "http://${SERVER##*@}:8080/health" -w '\n  HTTP %{http_code} (%{time_total}s)\n' || \
  echo "  WARNING: /health unreachable or degraded (HTTP 503) — check 'docker compose logs bot'"

echo "==> ✓ Deploy complete."
