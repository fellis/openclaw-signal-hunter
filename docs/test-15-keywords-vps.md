# Test 15 keywords (VPS)

Script to run on VPS: takes 15 keywords from the DB, optionally runs collection for them, counts signals and checks that everything needed for classification is present. The report is written to files next to the script.

## Run

Use the same environment where all Signal Hunter dependencies are already installed (same venv/container as the main app).

From the `signal-hunter` directory:

- **Report only** (no collection; uses already collected data):
  ```bash
  python scripts/test_15_keywords_vps.py
  ```

- **Collect then report** (run collection for these 15 keywords, then write the report):
  ```bash
  python scripts/test_15_keywords_vps.py --collect
  ```

Requires `DATABASE_URL` (and optionally `.env` and `config.json` in `signal-hunter`).

## Output

- **Report files** (next to the script in `scripts/`):
  - `test_15_keywords_vps_report.json` – full report (generated_at, keywords_selected, per_keyword, summary).
  - `test_15_keywords_vps_report.txt` – same data in a short human-readable form.

- **Stdout:** only the paths to these files, e.g.  
  `Report written to scripts/test_15_keywords_vps_report.json`

## Report fields

- **keywords_selected** – up to 15 keywords taken from `keyword_profiles` (first 15 by canonical_name).
- **per_keyword** – for each keyword:
  - `total` – number of raw_signals that have this keyword in `extra->keywords`.
  - `ready` – of those (in the fetched sample), how many are ready for classification (non-empty dedup_key and at least one of title/body).
  - `no_text` – of those, how many have no text (link-only, etc.).
- **summary** – over all fetched signals for these keywords:
  - `total_signals` – number of raw_signals in the sample.
  - `ready_for_classification` – count with text available for classification.
  - `no_text` – count without usable title/body.

"Ready for classification" means the same condition the embed/LLM pipeline uses: the record has `dedup_key` and `(title or body)` is non-empty after strip.
