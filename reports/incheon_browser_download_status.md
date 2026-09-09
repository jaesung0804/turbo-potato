# Incheon historical sale CSV import — 2026-09-09

Completed: official apartment-sale CSVs for Incheon 2012–2020 are now imported into `estate-capital-history-state`, commit `4c33f9eee4b0ed9b0dd0ed1bb9b894d2d8b5aa5d`.

The user supplied seven original annual files (2012–2018, 256,144 rows). Two prior official browser downloads supplied 2019–2020 (91,111 rows). Original CP949 bytes, repeated rows and cancellation-marked records are retained in deterministic gzip files; no duplicate annual partition was appended.

| Year | Raw rows | Source |
| --- | ---: | --- |
| 2012 | 20,553 | User-provided CSV |
| 2013 | 34,603 | User-provided CSV |
| 2014 | 42,320 | User-provided CSV |
| 2015 | 49,116 | User-provided CSV |
| 2016 | 42,929 | User-provided CSV |
| 2017 | 36,338 | User-provided CSV |
| 2018 | 30,285 | User-provided CSV |
| 2019 | 34,278 | Official browser download |
| 2020 | 56,833 | Official browser download |

Added: **9 files / 347,255 rows**. Incheon historical sales now cover **2006–2020, 15 files / 523,725 rows**, with no missing annual partitions in this interval. The shared checkpoint now contains **31 completed partitions / 3,003,844 raw rows** (including existing Gyeonggi sales and 2011 Gyeonggi rent).

## Validation and integration boundary

- Validated annual query metadata, Incheon province, all-district selection, apartment-sale schema, every contract date, positive sale price and floor area, sequential NO, and record widths.
- Compared each uploaded Git blob SHA and byte size with the locally compressed source. Confirmed manifest SHA, updated `state_files.json` alongside it, and read the published manifest after the branch update.
- `expected_count` records the observed CSV row count for cache compatibility. Each new entry explicitly sets `independent_server_count_checked: false`; no independent count-endpoint reconciliation is claimed.
- Existing completed partitions and checkpoint index entries were preserved. Import made no additional MOLIT network downloads. Rentals remain deferred by user instruction; the full 81-partition expansion is not marked complete.
- These are revised records available at extraction time, not historical publication vintages.
- Raw checkpoint import is complete. Model ingestion/retraining, backtests and website deployment have not been performed as part of this import.
