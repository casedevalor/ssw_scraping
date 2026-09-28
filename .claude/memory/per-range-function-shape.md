---
name: per-range-function-shape
description: Premise — every multi-range bot uses the split shape (_process_date_range + standard handler_download loop that re-raises when all ranges fail)
metadata:
  node_type: memory
  type: feedback
  originSessionId: 9937b7d0-6ad8-445f-86da-cf14a547d1b9
  modified: 2026-09-27T19:11:45.433Z
---

Premise (decided with the user on 2026-09-27): every bot with more than one date range uses the **split shape**:
- `_process_date_range(page, domain, dates, attempt, max_attempts)` handles one range; it `return`s on expected "no data" and `raise`s on any failure (timeout, unexpected message, missing download).
- `handler_download(page, domain, attempt, max_attempts)` is the standard loop: per-range `try/except` that logs one ERROR line, counts failures, and raises `RuntimeError("... All N date range(s) failed.")` when every range failed, so `main()` retries.

**Why:** The user's problem was lack of standardization. Mixed shapes (≈14 merged vs 6 split) made the code harder to read, and 9 bots silently skipped the "re-raise when all ranges fail" rule. The split shape puts that rule in an identical loop everywhere, keeps the per-range screen logic readable, and lets the loop be tested with a fake. It also opens the door to one shared loop in app/core later.

**How to apply:** 002–455F (including 435 per-option, 441, 442, 452, 455ABH, 455CGK, 455F, 467, 477, 493, 930 per-date) were converted on 2026-09-27. For 442 onward, convert each bot in the same pass as its column sentinel ([[column-sentinel-design]]) and update its 1DOCS doc. Every structural change means the whole bot file must be copied into each company's copy, after checking them for company-specific code.
