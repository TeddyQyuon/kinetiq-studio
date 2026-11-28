#!/usr/bin/env bash
# Run with --no-workers for just the API and UI, or --real-media to load Kokoro and WhisperX.
set -euo pipefail
root=$(cd -- "$(dirname -- "$0")/.." && pwd)
for py in python3 python; do
  if command -v "$py" >/dev/null && "$py" -c 'import sys; sys.exit(sys.version_info < (3, 12))'; then
    exec "$py" "$root/workers/dev_local.py" "$@"
  fi
done
echo 'Install Python 3.12 or newer to launch Kinetiq Studio.' >&2
exit 1
