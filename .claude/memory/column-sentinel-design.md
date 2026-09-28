---
name: column-sentinel-design
description: "Decisions behind the SSW column sentinel (per-company baselines, check after processing, upload still happens)"
metadata:
  node_type: memory
  type: project
  originSessionId: 9c0ead35-5358-4035-b9da-b32146ed035c
  modified: 2026-09-25T17:08:27.298Z
---

Column sentinel (`app/core/column_sentinel.py` + `config/sentinel/<ROUTINE>.yml`) decisions made 2026-09-24/25:
- Baselines are per company: each company's project copy keeps its own YAMLs; no generalized/shared sentinel (companies differ slightly).
- The check runs AFTER the routine's processing (known SSW renames are absorbed there, matching what Power BI expects), and it never blocks — report file and `<domain>.log` still go to SharePoint.
- YAML is the fixed contract, 1-based positions, strict numbering; log positions shown as Excel letters.
- Since 2026-09-27 SSW_SENTINEL itself is the user's "pasta padrão" (standard folder); see [[sentinel-is-standard-folder]].
- Done so far (2026-09-27): 002, 019, 036, 073 (FROTA/TERCEIROS), 076, 081, 103, 119, 200, 306, 322, 324, 411, 435, 441, 442, 452, 455ABH, 455CGK, 455F, 458 (dormant: scraped table), 467, 477, 493, 930. 455ABH/455CGK were converted to the split shape too; 571 is NOT guarded by decision (pure .sswweb uploaded as-is, no header — recorded in bot_571.md / column_sentinel.md); 056 not evaluated yet, in routine order.
- Dormant baselines (user decision 2026-09-27): when processing forces the header (fixed config list or hard-coded names — 103, 119, 324, 441, 455CGK, 458), still create the baseline and hook, but mark it "⚠️ Dormant today" in the routine doc and in column_sentinel.md's "Dormant baselines" table. Reason: the fixed-column method may be replaced later, and the sentinel should already be in place. The sentinel reads .xlsx too (119/324). Docs: 1DOCS/column_sentinel.md + a 'Column Sentinel' section in each bot_XXX.md and app_core.md — keep them in sync when adding a routine. Open decisions offered but not answered: raise DOWNLOAD_TIMEOUT_MS 120s→300s (MAX_REFRESH_ATTEMPTS was raised 30→120 on 2026-09-27); make processing_csv_455CGK tolerate a missing column instead of KeyError.

Related: [[log-messages-simple]].
