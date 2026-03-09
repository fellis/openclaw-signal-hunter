# Cleanup after backfill matched_rules is done

When the backfill has finished (no more relevant signals with empty `matched_rules`), you can remove the backfill-specific code to keep the codebase clean. This document lists what to delete and in what order.

**Do not remove:** `update_processed_signal_rule_match`, the `best_rule` field in `_classify_vectors` result, or the second pass in the embed worker (LLM-relevant rule match). Those are part of the normal pipeline.

---

## 1. Embed worker: third pass (backfill)

**File:** `signal-hunter/core/embed_worker.py`

Remove the block that runs the backfill batch each tick and the `backfill_rule_matched` entry in the return dict.

**Remove:**
- The comment and block starting with `# Third pass: backfill matched_rules...` through `if backfill_done: log.info(...)` (the whole `backfill_per_tick` / `backfill_done` block).
- In the `return { ... }` dict, remove the line `"backfill_rule_matched": backfill_done,`.

---

## 2. EmbedProcessor: backfill batch and helper

**File:** `signal-hunter/core/embed_processor.py`

Remove the backfill-only methods.

**Remove:**
- The section `# ------------------------------------------------------------------\n# Backfill matched_rules (one batch; ...)\n# ------------------------------------------------------------------` and everything in it:
  - `def _text_for_backfill(self, row: ...) -> str: ...`
  - `def run_backfill_rule_match_batch(self, limit: int, *, dry_run: bool = False) -> int: ...`
- Stop at the next section `# Private: build ProcessedSignal`.

---

## 3. Storage: backfill queries

**File:** `signal-hunter/storage/postgres.py`

Remove the two methods used only for the backfill.

**Remove:**
- `def count_relevant_empty_matched_rules(self) -> int: ...` (and its body up to the next method).
- `def fetch_relevant_empty_matched_rules_batch(self, limit: int, offset: int = 0) -> list[dict[str, Any]]: ...` (and its body up to the next method).

---

## 4. Config example

**File:** `signal-hunter/config.example.json`

In the `"processor"` object, remove the line:
- `"backfill_rule_match_per_tick": 64`

Optionally remove `"llm_relevant_rule_match_per_tick": 50` only if you added it solely for this backfill (otherwise leave it). On a live VPS, remove or set to `0` the same key in `config.json` if you had set it there.

---

## 5. Backfill script (optional)

**File:** `signal-hunter/scripts/backfill_matched_rules.py`

- **Option A:** Delete the file if you do not plan to run this migration again.
- **Option B:** Move to something like `docs/archive/backfill_matched_rules.py` or leave in `scripts/` with a one-line comment at the top: "One-off migration; safe to remove after backfill is done."

---

## Verification after cleanup

1. Run tests (if any) for the embed worker and EmbedProcessor.
2. Confirm the embed worker starts and runs its main loop and second pass (LLM-relevant rule match) without errors.
3. In the DB, `SELECT COUNT(*) FROM processed_signals WHERE is_relevant = true AND (matched_rules IS NULL OR jsonb_array_length(matched_rules) = 0);` should already be 0 before cleanup; no new rows with empty `matched_rules` should appear for normal processing.
