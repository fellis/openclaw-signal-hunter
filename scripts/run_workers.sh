#!/usr/bin/env bash
# Worker runner: each worker has its own loop and interval. All run in parallel.
# Single process (lock file), multiple background loops. Used by signal-hunter-workers container.

set -e
LOCK_FILE="${LOCK_FILE:-/tmp/signal-hunter-workers.lock}"
INTERVAL_WORKER="${SH_WORKER_INTERVAL:-60}"
INTERVAL_COLLECT="${SH_COLLECT_INTERVAL:-300}"

# Plugin root (directory containing skill/, scripts/)
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

acquire_lock() {
  if [[ -f "$LOCK_FILE" ]]; then
    local pid
    pid=$(cat "$LOCK_FILE" 2>/dev/null)
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
      echo "Already running (PID $pid). Exiting."
      exit 0
    fi
  fi
  echo $$ > "$LOCK_FILE"
  trap 'rm -f "$LOCK_FILE"' EXIT
}

main() {
  acquire_lock
  echo "Worker runner started (PID $$). Translate/LLM/Embed/Vectorize: every ${INTERVAL_WORKER}s. Collect: every ${INTERVAL_COLLECT}s."

  # Each worker: own loop, own interval (parallel)
  ( while true; do python -m skill run_translate_worker || true; sleep "$INTERVAL_WORKER"; done ) &
  ( while true; do python -m skill run_collect_worker   || true; sleep "$INTERVAL_COLLECT"; done ) &
  ( python -m skill run_worker_daemon ) &
  ( while true; do python -m skill run_embed_worker     || true; sleep "$INTERVAL_WORKER"; done ) &
  ( while true; do python -m skill embed                || true; sleep "$INTERVAL_WORKER"; done ) &

  wait
}

main
