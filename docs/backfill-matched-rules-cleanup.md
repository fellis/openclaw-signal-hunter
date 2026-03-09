# Cleanup after backfill matched_rules is done

When the backfill has finished (no more relevant signals with empty `matched_rules`), you can remove the backfill-specific code to keep the codebase clean. This document lists what to delete and in what order.

**What we changed for the backfill:** Besides adding the third pass (backfill batch), we changed the embed worker so it no longer exits early when there are no unprocessed signals. Now it still loads the processor and runs the second pass (LLM-relevant rule match) and the third pass (backfill) even when `unprocessed == 0`. That way the backfill runs every tick until the queue is empty. To restore the worker to its original behaviour, you need to revert that logic as well (see section 1).

**Do not remove:** `update_processed_signal_rule_match`, the `best_rule` field in `_classify_vectors` result, or the second pass in the embed worker (LLM-relevant rule match). Those are part of the normal pipeline.

---

## 1. Embed worker: third pass (backfill) and pass logic

**File:** `signal-hunter/core/embed_worker.py`

**1.1 Remove the third pass (backfill)**  
- The comment and block starting with `# Third pass: backfill matched_rules...` through `if backfill_done: log.info(...)` (the whole `backfill_per_tick` / `backfill_done` block).
- In the `return { ... }` dict, remove the line `"backfill_rule_matched": backfill_done,`.

**1.2 Restore original pass logic**  
Originally the worker returned immediately when there were no unprocessed signals and did not run the second or third pass. Restore that behaviour:

- After `unprocessed = self._storage.count_unprocessed()`, add back the early return:
  - `if unprocessed == 0: log.info("[embed_worker] no unprocessed signals, idle"); return {"status": "idle", "note": "No unprocessed signals."}`.
- Remove the branch that runs when `unprocessed == 0`: the `else:` that logs "no unprocessed signals; running LLM rule-match and backfill passes only" and the corresponding structure so that the second pass (and any code after it) runs only when `unprocessed > 0` (i.e. after a successful `process_all` or equivalent). In other words: ensure that loading rules, creating the processor, and running the second pass happen only when `unprocessed > 0`; when `unprocessed == 0` the method exits at the top with the idle return.

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
- `"backfill_rule_match_per_tick": 256`

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
