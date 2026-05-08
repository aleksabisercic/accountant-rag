#!/usr/bin/env bash
# Migrate runtime data files from local repo to mari-mar-bot Hetzner server.
# DESTRUCTIVE — overwrites server-side state.
#
# Usage:
#   ./scripts/sync-data.sh acl.json invites.json     # plain JSON, no downtime
#   ./scripts/sync-data.sh analytics.db              # SQLite, ~5s downtime for atomic swap
#   ./scripts/sync-data.sh acl.json analytics.db     # mixed, handles each appropriately
#
# Files are looked up under data/ locally and copied to /opt/mari-mar-bot/data/ on server.

set -euo pipefail

SERVER="${MARI_MAR_BOT_SERVER:-deploy@178.105.109.233}"
REPO_PATH="/opt/mari-mar-bot"

if [ $# -eq 0 ]; then
  echo "Usage: $0 <file1> [file2 ...]"
  echo "Files relative to data/. Examples: acl.json, invites.json, analytics.db"
  exit 1
fi

echo "==> About to overwrite on $SERVER:"
for f in "$@"; do
  if [ ! -f "data/$f" ]; then
    echo "  ✗ Missing local: data/$f"
    exit 1
  fi
  echo "  - data/$f → $REPO_PATH/data/$f"
done
read -r -p "Continue? [y/N] " yn
[[ "$yn" =~ ^[yY] ]] || { echo "Aborted."; exit 1; }

# Group: SQLite files need stop/start cycle, JSON files don't.
SQLITE_FILES=()
PLAIN_FILES=()
for f in "$@"; do
  if [[ "$f" == *.db ]]; then
    SQLITE_FILES+=("$f")
  else
    PLAIN_FILES+=("$f")
  fi
done

# Plain files: scp directly, bot reads on next access (or restart for ACL).
for f in "${PLAIN_FILES[@]}"; do
  echo "==> Syncing $f (no downtime)"
  scp "data/$f" "$SERVER:$REPO_PATH/data/$f"
done

# SQLite: snapshot via Python sqlite3.backup, atomic swap with bot stopped.
if [ ${#SQLITE_FILES[@]} -gt 0 ]; then
  echo "==> Snapshotting SQLite files"
  for f in "${SQLITE_FILES[@]}"; do
    .venv/bin/python -c "
import sqlite3, sys
src = sqlite3.connect('data/$f')
dst = sqlite3.connect('data/$f.export')
with dst:
    src.backup(dst)
src.close(); dst.close()
print('  snapshot: data/$f.export ('+str(dst.execute and 'ok' or '')+')')
"
    scp "data/$f.export" "$SERVER:$REPO_PATH/data/$f.new"
    rm "data/$f.export"
  done

  echo "==> Swapping with bot stopped (~5s downtime)"
  SWAP_CMDS=""
  for f in "${SQLITE_FILES[@]}"; do
    SWAP_CMDS+="mv data/$f.new data/$f && rm -f data/$f-wal data/$f-shm && "
  done
  ssh "$SERVER" "cd $REPO_PATH && \
    docker compose stop bot && \
    ${SWAP_CMDS}true && \
    docker compose start bot && \
    sleep 4 && \
    docker compose ps --format 'table {{.Name}}\t{{.Status}}'"
fi

# If only plain JSON synced, suggest restart to load ACL/invites
if [ ${#SQLITE_FILES[@]} -eq 0 ] && [ ${#PLAIN_FILES[@]} -gt 0 ]; then
  echo "==> Plain files updated. ACL/invites are loaded on bot startup — restart to apply:"
  echo "    ssh $SERVER \"cd $REPO_PATH && docker compose restart bot\""
fi

echo "==> ✓ Sync complete."
