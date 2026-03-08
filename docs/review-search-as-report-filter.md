# Code review: Search as Report filter

Review of the change that moved text/semantic search into the Report page as a filter and removed the standalone Search page.

---

## What was done

- Backend: `get_search_result_ids()` in `search.py`; report and clusters accept `q` + `search_mode` and restrict by search IDs.
- Frontend: Report reads `q`/`search_mode` from URL, FilterPanel has search block, SignalTable passes search params to cluster fetch; Search page removed, `/search` redirects to `/report`.

---

## Correctness

**Backend**

- **Text path**: `get_search_result_ids` uses same filters as report (sources, keywords, dates) and `JOIN embedding_queue` so only vectorized signals are considered. Matches report semantics.
- **Semantic path**: Uses `_embed_query` + `_qdrant_search`, then resolves IDs via `raw_signals.url`. Qdrant does not filter by `date_from`/`date_to` (payload filter has no date); date filtering still happens in `_fetch_signals`, so the final report is date-filtered. No new bug.
- **Empty search**: When `search_ids` is empty we return `{ total_signals: 0, categories: [] }` / `{ clusters: [] }` and cache that. Correct.
- **Cache keys**: Report and clusters cache keys include `q` and `search_mode`; no cross-talk between search and non-search responses.

**Frontend**

- **URL sync**: `searchQuery` and `searchMode` come from `searchParams`; `load` depends on them, so changing search or filters refetches. Subtitle shows "N results for \"q\"" when `searchQuery` is set.
- **Clusters**: `searchQuery`/`searchMode` are passed into `SignalTable` and then into `fetchClusters` on expand; backend receives them and restricts by the same search. Consistent.
- **Redirect**: `SearchRedirect` maps `mode` to `search_mode` and keeps the rest of the query string; old `/search?q=...&mode=text` links work.

---

## Edge cases and minor points

1. **Clearing search in UI**  
   If the user clears the input and does not press Enter/Search, the URL still has `q=...`, so the report keeps showing search results. To “clear search” they must run search with an empty string (not possible with current “min 2 chars” rule) or change URL. Optional improvement: a “Clear search” control that calls `onSearchChange('', 'semantic')`.

2. **Semantic: order of IDs**  
   `get_search_result_ids` for semantic does `SELECT id::text FROM raw_signals WHERE url = ANY(%s)`; order of rows is undefined. The report only needs the set of IDs; ordering is done later by category/rank. No bug.

3. **FilterPanel toggle with empty input**  
   Clicking Semantic/Text calls `onSearchChange(queryInput, mode)`. If the user has not typed anything, this can set `search_mode` in the URL and clear `q`. Next time they type and search, the chosen mode is used. Acceptable.

4. **Backend `/search` route**  
   `app.get("/search")` still serves the SPA so that client-side redirect works. No change needed.

---

## Consistency

- Text search in `get_search_result_ids` uses the same filters (sources, keywords, dates) as the existing text search endpoint; it does not add confidence/intensity (those are applied later in `_build_where` for `_fetch_signals`). Same as before.
- Semantic path does not use date in Qdrant; date is applied in report/clusters via `_build_where`. Aligned with existing semantic endpoint.

---

## Security and performance

- No new user input in SQL beyond what was already there; `q` is used in ILIKE with `%q%` (no raw concatenation of multiple statements). Same as existing search.
- Search is run on every report and on every cluster expand when `q` is set (no cache of ID list). Acceptable per product decision; can be revisited if load grows.

---

## Verdict

Implementation is correct and consistent with the rest of the app. No blocking issues. Optional improvement: add an explicit “Clear search” in the Report/FilterPanel UX.
