#!/usr/bin/env bash
# Worker runner: runs LLM worker, embed worker, collect worker, and embed (vectorize) in a loop.
# Single process, one instance per host (lock file). Used by signal-hunter-workers container.

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

run_tick() {
  # Run quick workers first so they are not blocked by long-running LLM/embed
  python -m skill run_translate_worker || true
  python -m skill run_collect_worker   || true
  python -m skill run_worker           || true
  python -m skill run_embed_worker     || true
  python -m skill embed                || true
}

main() {
  acquire_lock
  echo "Worker runner started (PID $$). Interval: ${INTERVAL_WORKER}s (translate, collect, LLM, embed, vectorize)."
  while true; do
    run_tick
    sleep "$INTERVAL_WORKER"
  done
}

main
