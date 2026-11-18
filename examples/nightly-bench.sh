#!/usr/bin/env bash
# Nightly benchmark of the local model: skips quietly when the backend is down, keeps one JSON
# report per night, never blocks anything. Schedule it with examples/deputy-nightly-bench.plist.
set -euo pipefail
OUT="${DEPUTY_NIGHTLY_DIR:-$HOME/.local/state/deputy/nightly}"
mkdir -p "$OUT"
if ! deputy doctor 2>&1 | grep -q "^backend .*: ok"; then
  echo "$(date -u +%FT%TZ) backend not answering, benchmark skipped" >> "$OUT/skipped.log"
  exit 0
fi
deputy bench --runs 3 --run-code --json > "$OUT/$(date +%F).json.tmp"
mv "$OUT/$(date +%F).json.tmp" "$OUT/$(date +%F).json"
find "$OUT" -name '20*.json' -mtime +90 -delete
