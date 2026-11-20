#!/usr/bin/env bash
# Grab GPL's latest posts from Facebook and push any changes to GitHub.
# Cron runs this every 15 minutes. See README for setup.
set -euo pipefail
cd "$(dirname "$0")"

# Don't start if the previous run is still going.
exec 9>/tmp/gpl-capture.lock
flock -n 9 || exit 0

git pull -q --rebase
python3 capture.py
git add data/posts.json
if ! git diff --cached --quiet; then
  git commit -qm "Capture posts $(date -u +%FT%TZ)"
  git push -q
fi
