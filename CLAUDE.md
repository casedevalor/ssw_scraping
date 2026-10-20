# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Automation suite for **Apsesoft/Softweb**: async Playwright bots that log into the **SSW** transport-management web system, run a report (a numbered "rotina", e.g. `442`), download the resulting file, and upload it to **SharePoint** via the Graph API (MSAL). One deployment of this repo serves one SSW domain/company (`SSW_DOMAIN` in `.env`, e.g. `AL2`, `IVC`, `AGE`).

**This folder (`SSW_SENTINEL`) is the user's standard project folder ("pasta padrão")** — since 2026-09-27; before that it was only a test bed. Changes made here are the reference version and are then carried **by hand** to each company's copy. Never assume a company's files are identical to these: when a change is done, always finish by telling the user exactly what to **copy** (new files) and what to **edit by hand** (small, copyable diffs for existing files, which may carry company-specific code).

Talk to the user in **Portuguese**; code, identifiers, docstrings and comments stay in **English**.

## Commands

```bash
# Install deps (uv is the package manager; do not use bare pip/venv)
uv sync
uv run playwright install chromium   # first-time browser install

# Run one or more routines by ID — each runs isolated, failures don't block the rest
uv run python main.py 442
uv run python main.py 076 930 455ABH

# Run a workflow (grouped block of routines, see app/routines/wf_*.py)
uv run python main.py WF_DAILY
uv run python main.py WF_ALL
uv run python main.py WF_WE

# Syntax-check a single bot file after editing (no test suite in this repo)
uv run python -m py_compile app/routines/bot_442.py
```

There is no lint/test/CI config in this repo — validation is `py_compile` plus manual/headed runs. For isolated checks (e.g. the column sentinel against synthetic CSVs), write scripts in the scratchpad and run them with `PYTHONPATH=<repo> uv run --project <repo> python script.py` — the logger writes to `output/logs/` under the **current working directory**, so running from the scratchpad keeps the real logs clean. Production runs are triggered by `scripts/scheduler.sh` via cron, calling `main.py` with a workflow ID.

## Architecture

- **`main.py`** — the only real entrypoint. `ROUTINE_MAP` maps a routine ID string (e.g. `"442"`) or workflow ID (`"WF_DAILY"`, `"WF_ALL"`, `"WF_WE"`) to a dotted `module.main` path. It imports the module dynamically at call time and awaits `main()` — routines are never imported eagerly, and **must not call `asyncio.run()` at module level** (that breaks dynamic import). Every new `bot_XXX.py` must be registered here to be runnable.
- **`app/routines/bot_XXX.py`** — one file per SSW routine, all following the same shape (see "Bot file pattern" below). `wf_daily.py` / `wf_startall.py` / `wf_weekend.py` are thin schedulers: each defines named blocks of `(routine_id, "app.routines.bot_XXX.main")` tuples and picks a block based on `datetime.now()` (hour/weekday), then runs them sequentially via the same dynamic-import approach as `main.py`.
- **`app/core/`**:
  - `login.py` — `perform_login`, retried up to `MAX_LOGIN_ATTEMPTS`.
  - `upload_to_sharepoint.py` — `upload_report_file`, `upload_log_file`; routes files to `Banco_de_Dados/<domain>/<routine>` on SharePoint, with routine-specific folder overrides in `_resolve_remote_folder`.
  - `data_processing.py` — `Dataframe_Analytics`, one `processing_csv_XXX`/`processing_sswweb_XXX` method per routine that needs post-download Pandas cleanup (column filtering, encoding fixes, etc.). Not every routine has one — only add it if the raw download genuinely needs reshaping.
  - `downloads.py` — `process_156_report(page, date, routine, domain, attempt, max_attempts, unit=None)`: shared monitor/download/upload flow for any routine that gets queued onto SSW's "screen 156" instead of downloading immediately. **Gotcha:** file naming is driven by a `ROUTINE_HANDLERS` dispatch dict + `_build_file_name()` inside this same file — a routine not registered there silently falls through to `_handle_default`, which produces a name with no unit/date encoded. Whenever a new 156-routed routine is added, add both a `_handle_XXX` function (copy `_handle_076` if the routine is per-unit) **and** a branch in `_build_file_name()`, and register it in `ROUTINE_HANDLERS`. A file with several header bands (916) gets one baseline per band, listed in `SENTINEL_BASELINES` right below `ROUTINE_HANDLERS`. Keep the `_handle_XXX` functions and the `ROUTINE_HANDLERS` entries in **ascending routine order** (`_handle_default` last), so a routine is easy to find. After naming/processing and the upload, `process_156_report` runs the column sentinel for any routine that has a baseline (see "Column Sentinel" below).
  - `column_sentinel.py` — `check_file(file_path, name, domain)` / `has_baseline(name)`: compares a downloaded file's header with `config/sentinel/<name>.yml` and writes one line to the domain log. Never raises, never blocks the upload. See "Column Sentinel" below and `1DOCS/column_sentinel.md`.
- **`app/integrations/`** — external API clients: `sharepoint.py` (`SharePointApi`, singleton via `get_sharepoint_client()`, MSAL confidential-client auth to Graph API) and `recaptcha.py` (anticaptcha solving for the SSW login captcha).
- **`app/utils/`**:
  - `custom_logger.py` — `CustomLogger` singleton via `get_logger()`. `.log(level, domain, message)` prepends an entry to `output/logs/<domain>.log`, capped at `MAX_LOG_LINES` (1000); `.debug(message, level=...)` prints color-coded to the terminal **only** — these are separate channels, don't confuse them. Level names are 4-char abbreviations everywhere (`DBUG`/`INFO`/`SNTL`/`WARN`/`ERRO`/`CRIT` — `SNTL` is the custom level `SENTINEL = 25`, used only for the column sentinel's `OK` line; DRIFT stays `WARN`, ERROR stays `ERRO`) — passing a full word like `level="ERROR"` to `.debug()` silently falls back to the wrong color/stream. `.log()` collapses every message to one line (drops Playwright's `=== logs ===` / `Call log:` block) before writing. `clear_log(domain)` wipes the file **then** logs the "cleared" notice (in that order — reversing it erases the notice, which is what caused `wf_daily.py`'s scheduled Monday–Friday 1 AM clear to leave domain logs permanently empty until this was fixed). **Company copies may still run an older logger that caps the file at 100 lines** (`if len(existing_logs) > 99`) — high-volume routines (076 ≈ 2 lines per unit per run, 019 one per unit) push history out; point it out when relevant, the fix is raising that cap.
  - `datetime_utils.py` — `Dates` class, the shared library of date-range calculators (`growing_days()`, `last_month()`, `calculate_two_months()`, `current_day()`, `calculate_initial_to_current_dates()`, etc.). Always reuse these instead of hand-rolling date math in a bot.
  - `cleanup.py` — `GarbageCollector().delete(path)` sends downloaded files to the OS trash, not a permanent delete.
- **`config/settings/variables.py`** — the single settings source. `AppSettings` (pydantic-settings) loads secrets from `config/credentials/.env` (`SSW_*`, `TENANT_ID_*` / `CLIENT_ID_*` / `CLIENT_SECRET_*` / `SHAREPOINT_*_*` — the suffix varies per deployment, e.g. `_SWT268` here) and nests `config/settings/config.yml` (per-bot lists/columns like `list_units`, business ranges) under `settings.yaml_config.variables.bot_XXX`. Adding a new bot-specific YAML param means: add the field to a `BotXXX(BaseModel)` class, register it on `VariablesConfig`, then add the value to `config.yml`. Import `settings` from this module everywhere — never read `.env`/`config.yml` directly in a bot, and never import flat module-level names like `SSW_DOMAIN` (that was the old, now-gone pattern — see below). Keep the `bot_XXX` blocks in **ascending routine order** in `config.yml`, and the `BotXXX` classes / `VariablesConfig` fields in `variables.py` in the same order. Only `config/settings/config.yml` is read — the `config.yml` at the repo root is not loaded by any code. Unused blocks are removed, not left behind (`ranges.range_days` and `bot_402` were dropped on 2026-09-25).
- **`1DOCS/bot_XXX.md`** — one doc per routine, written in **English** (matches code comments/docstrings), except literal SSW screen messages, which stay quoted in Portuguese with a short English gloss (they're the real strings the system returns). **Whenever a routine is created or significantly changed, creating/updating its doc — and linking it from `README.md`'s routine table — is implicit, not something that needs to be asked for separately.** Structure (see [bot_019.md](1DOCS/bot_019.md) or [bot_002.md](1DOCS/bot_002.md) as references). Exception: `1DOCS/app_core.md` is written in Portuguese — keep it that way when editing it:
  - Title only (`<h3># 📄 Routine XXX</h3>`) — **no company/domain banner** at the top. A handful of older docs still carry that banner; strip it opportunistically when that routine is next revisited, not in a bulk pass.
  - **Business Rule (Dates)** or **Business Parameter** as a table/short section (condition → range → `Dates()` function, or whatever non-date parameter drives the routine) — not prose.
  - **Execution Flow** as a Mermaid `flowchart` mirroring the real control flow (including the `asyncio.wait` races and any multi-click quirks), not a bullet summary.
  - **Screen Selectors** as a table (field | selector | note).
  - **Error Handling** as a table (scenario | trigger | action) — one row per branch in the code, including the `MSG_NOT_RELEASED` early-abort.
  - **Resilience** section stating the retry count, backoff, and what the single end-of-run cleanup does.
  - **🛡️ Column Sentinel** (only for routines with a baseline), placed right before **Output**: baseline file(s), column count, header line, where the check runs, what processing runs before it, a note, and an example `OK` log line. The flowchart's download node also mentions "check columns" after the upload.
  - Closing: **Related Files** (including `app/core/column_sentinel.py` and `config/sentinel/<name>.yml` when guarded), **Example Run**, link back to the README.
  - `bot_704.md` and `bot_930_r.md` are orphaned docs with no matching `bot_*.py` — don't use them as a reference, and check before writing a doc for a routine ID that doesn't exist in `app/routines/`.
- **`output/downloads/<ROUTINE>/`** and **`output/logs/<domain>.log`** — runtime artifacts, cleaned up by `GarbageCollector` at the end of each bot's `main()`.

## Bot file pattern (`app/routines/bot_XXX.py`)

All bots converge on the same structure — match it for new/edited bots (see [bot_019.py](app/routines/bot_019.py), [bot_002.py](app/routines/bot_002.py), [bot_411.py](app/routines/bot_411.py) as clean references):

1. **Header**: module-level `cwd = Path.cwd()`, `log = get_logger()`, `ROUTINE: str = "XXX"`. **The `ROUTINE` constant must match the filename** — a copy-paste from another routine that leaves the old `ROUTINE` value in place is a real, silent bug that has happened in this repo before (`bot_019.py` briefly ran `bot_477.py`'s exact logic because `ROUTINE` was still `"477"`). Sanity-check with:
   ```bash
   grep -oP 'ROUTINE:\s*str\s*=\s*"\K[^"]+' app/routines/bot_XXX.py   # should print XXX
   ```
2. **Selectors as named constants** (`SEL_DATE_START`, `SEL_ROUTINE_INPUT`, `SEL_ERROR_LABEL`, `SEL_BTN_OK`, …) and known SSW screen messages (`MSG_NOT_RELEASED = "Opção não liberada"`, `MSG_NO_RECORDS`, …) — never inline raw selector strings/text in the logic.
3. **Date logic**, when applicable, isolated in a small helper (`_build_date_range`/`_build_date_ranges`) built on `Dates()`. This is mandatory for **any** routine with a date-range filter, even one with zero day/hour variation — a routine calling a single unconditional `Dates()` method with no gate still gets the helper, wrapping that exact call one-to-one (see `bot_103.py`, `bot_322.py`); "no gate" only affects the helper's *body* (a plain wrap, no `if`), never whether the helper exists. Only skip the helper for a routine with **no date filter at all** (e.g. `bot_019.py` is purely per-unit, `bot_435.py` is purely per-option) — don't invent one there.
   The current multi-range default is an unconditional wrap of `Dates().previous_and_current_month_ranges()` — no day/hour gate, always both `[previous month (full), current month (to date)]` — used by `bot_002.py`, `bot_036.py`, `bot_073.py`, `bot_076.py`, `bot_119.py`, `bot_200.py`, `bot_324.py`, `bot_411.py`, `bot_442.py`, `bot_452.py`, `bot_467.py` and `bot_493.py`. The business rule is "always backfill last month," not "only early in the month"; the older day/hour gates are gone. **Exception — the 455 weekend backfill (restored at the user's request on 2026-10-12):** `bot_455ABH.py` (Saturday), `bot_455CGK.py` (Sunday) and `bot_455F.py` (Saturday) return `Dates().previous_twelve_months_ranges()` (12 full months, current month excluded) when `weekday() == BACKFILL_WEEKDAY and hour == BACKFILL_HOUR` (module constants, default 15h), otherwise `previous_and_current_month_ranges()`. `BACKFILL_HOUR` must match the cron time of `WF_WE` in that company's `scripts/scheduler.sh` (this folder's scheduler still fires it at 17:00). Because that helper returns a **list**, the handler must loop over it (per-range isolation; re-raise if every range failed so `main()`'s retry engages) — a handler that still treats the result as a single `(start, end)` tuple is a bug (`fill(dates[0])` would receive a tuple). Routines with their own no-gate window (`bot_103.py`, `bot_916.py` — previous month + current month up to **yesterday**, via `previous_and_current_month_to_yesterday_ranges()`, which returns only the previous month on the 1st; user rule, 2026-10-20 — `bot_306.py`, `bot_322.py`, `bot_441.py`, `bot_477.py`, `bot_571.py`, and `bot_458.py`/`bot_930.py` — yesterday + today, each date on its own; changed on 2026-09-27 at the user's request) are **not** in this group; don't add a routine to it without checking its actual condition first. **Only add a gate to a routine that already has one or where it's explicitly requested** — when a routine's existing date call already returns a fixed set of ranges with no day-dependent branch, just wrap that exact call in the helper and note in its docstring why there's no gate (see `bot_103.py`) — don't invent a new gate/behavior change on your own judgment.
4. **Multi-range shape (premise, decided with the user on 2026-09-27):** every routine with more than one date range is split in two — `_process_date_range(page, domain, dates, attempt, max_attempts)` handles **one** range (fills the form, races the outcome, saves/uploads/checks; `return`s on an expected "no data" message and **raises** on any failure: timeout, unexpected message, missing download), and `handler_download(page, domain, attempt, max_attempts)` is the **standard loop**, identical in every routine:
   ```python
   date_ranges: List[Tuple[str, str]] = _build_date_ranges()
   failures: int = 0
   for dates in date_ranges:
       log.log(level=logging.INFO, domain=domain,
               message=f"{domain} ROUTINE {ROUTINE} | Start Dates: [{dates[0]} → {dates[1]}]")
       try:
           await _process_date_range(page, domain, dates, attempt, max_attempts)
       except Exception as e_range:
           failures += 1
           log.log(level=logging.ERROR, domain=domain,
                   message=f"{domain} ROUTINE {ROUTINE} | Failed to process [{dates[0]} → {dates[1]}]: {e_range}")
   if failures == len(date_ranges):
       raise RuntimeError(f"{domain} ROUTINE {ROUTINE} | All {len(date_ranges)} date range(s) failed.")
   ```
   Exceptions raised inside `_process_date_range` don't repeat the range (the loop's log line already has it). Routines that loop over something else keep the same idea and naming: `_process_unit_range` for (range, unit) pairs (`bot_076.py`), `_process_option` for report options (`bot_435.py`), `_process_date` for single days (`bot_930.py`; `bot_458.py` also opens a fresh routine popup per date, because its race waits on a result row that would otherwise still be on screen from the previous date). `bot_322.py` is the reference. 002–455F follow it (455ABH/455CGK were converted on their own, since their sentinel predated this premise); from 458 on, convert each routine in the same pass as its column sentinel — a structural change, so the whole bot file is copied to each company's copy (after checking it for company-specific code).
   **The per-range body** (`_process_date_range`, `check_and_download_report` for single-range routines, or per-unit loop bodies) fills the form, then races the outcome instead of clicking-and-hoping —
   ```python
   task_download = asyncio.create_task(page.wait_for_event("download"))
   task_msg      = asyncio.create_task(page.locator(SEL_ERROR_LABEL).wait_for(state="visible"))
   await page.get_by_role("link", name=SEL_BTN_EXCEL).click()
   done, pending = await asyncio.wait([task_download, task_msg], timeout=60.0, return_when=asyncio.FIRST_COMPLETED)
   for task in pending: task.cancel()
   await asyncio.gather(*pending, return_exceptions=True)
   ```
   **The download-wait timeout is at least `60.0`s** — SSW can be slow to generate/deliver a file, and a shorter timeout (several older bots still use `15.0`) causes false-positive `TimeoutError`s on a report that was simply still generating. Bump it opportunistically when a bot is next touched, not in a bulk pass. This does not apply to the menu-level popup/error race (`timeout=10.0`), which is about whether the popup opens, not file delivery.
   Then branches: no task done → `TimeoutError`; download done → save to `cwd/"output"/"downloads"/ROUTINE`, `await upload_report_file(...)`, then run the column sentinel if the routine has a baseline (`check_file(path_download / file_name, ROUTINE, domain)`) — no extra "success" line after it (see "Log messages"); message done → read `inner_text()`, dismiss with `SEL_BTN_OK`, log WARNING (and branch further on known `MSG_*` text when the screen has multiple outcomes). A few screens (e.g. `bot_019.py`) show no message at all on success and instead need a **second click** on the same submit link to actually trigger the download — don't assume every screen signals success explicitly.
5. **`run(playwright, attempt, max_attempts)`**: launches Chromium (`headless=getattr(settings, "headless", True), args=["--no-sandbox", "--disable-dev-shm-usage"]`), opens a context/page, `perform_login(page, ROUTINE)`, fills the routine-number field, then races `popup` vs the error label the same way as step 4 before delegating to `handler_download`. The race is preceded by a short *why* comment — not just the code — explaining that a routine that is `MSG_NOT_RELEASED` for this domain never opens a popup, only the error label (see `bot_441.py`/`bot_442.py` for the exact wording); a race with no such comment above it is incomplete, not just terse. For per-unit routines, this race + form-fill happens **inside** the per-unit loop (a fresh popup opens every unit — close it explicitly after use) rather than once before it; check whether the target SSW screen reuses one popup for the whole list (`bot_076.py`) or opens a new one per unit (`bot_081.py`, `bot_019.py`) before copying either shape. Errors are logged with domain+routine+attempt context and **re-raised** (never swallowed) so `main()`'s retry loop sees them; `browser`/`context` are closed in a `finally`.
6. **`main()`**: fixed `MAX_ATTEMPTS = 3` retry loop around `run()`; if the caught error contains `MSG_NOT_RELEASED`, log WARNING and stop retrying (the option genuinely isn't available — retrying won't help); on the last attempt, log ERROR "MAXIMUM ATTEMPTS REACHED"; otherwise log INFO and `asyncio.sleep(5)` before the next attempt. A `finally` block always runs once at the end (not per attempt), identical in every bot: `GarbageCollector().delete(cwd / "output" / "downloads" / ROUTINE)`, then `upload_log_file(domain=domain, routine=ROUTINE)` (via `run_in_executor` since it's sync; it logs its own `Log file ... uploaded to '<tenant>/Banco_de_Dados/BOTLOGS'` line), each in its own `try/except` so a cleanup failure doesn't mask the routine's real outcome, and **last** `<DOMAIN> ROUTINE <ROUTINE> | Main routine completed.` — nothing from the run may be logged after it (user rule, 2026-09-29). The retry line is `<DOMAIN> ROUTINE <ROUTINE> | Attempt N failed. Retrying in 5s...`.
7. **No `asyncio.run(main())` at module level** — `main.py`/`wf_*.py` import and call it dynamically; a stray module-level call breaks that.
8. Register the routine in `main.py`'s `ROUTINE_MAP` (and in a `wf_*.py` block if it should run on a schedule). If the routine can be queued onto screen 156, also register it in `app/core/downloads.py`'s `ROUTINE_HANDLERS` (see the `downloads.py` gotcha above).

Logging convention throughout: `log.log(level=logging.INFO|WARNING|ERROR, domain=settings.ssw_domain, message=f"{domain} ROUTINE {ROUTINE} | ...")` for anything that should persist to the file log; `log.debug(message, level=...)` only for ephemeral terminal-only traces, using the 4-char level names described above.

## Code style

- Strict type hints everywhere (parameters, returns, locals where non-obvious).
- Pydantic (`BaseModel`) for any structured config/data parsing — never hand-parse YAML/JSON.
- No silent `except: pass` — every caught exception is logged (file log, not just terminal) before being handled or re-raised.
- `asyncio` native for all Playwright/I/O; prefer Playwright's own waits (`wait_for_event`, `locator().wait_for(...)`) over fixed `sleep`s except for short UI-settle delays (`page.wait_for_timeout(...)`).
- Docstrings and inline comments in **English**, even though log/UI strings mirror the Portuguese SSW system. When revising a routine, comment the *why* on the parts that matter (why a race is used, why a modal gets dismissed, why an exception is swallowed vs. re-raised) — not a comment on every line, and not on trivial filler waits.
- **Touching a bot file is a premise to review its comments and docstrings, not just its code — every time, no exceptions.** Concretely, on every touch: (1) every docstring in the file is re-read against what the code actually does, not just the docstring nearest the edit; (2) every inline comment in the file is re-read the same way; (3) if the routine has a `_build_date_range`/`_build_date_ranges` helper, its docstring has an accurate NOTE (see below) — a date-helper with no NOTE is always wrong, never an acceptable state to leave behind. A stale docstring (a timeout value that no longer matches the code, a dead commented-out code block, a leftover `# FIX: ...` migration note describing a change that's no longer visible anywhere nearby) is a defect the same way a code bug is — fix it in the same pass, don't leave it for later. **"Touching" means any edit at all, including a single-value bump (e.g. a `15.0`→`60.0` timeout fix) — it is not scoped down to just the lines being changed.** A narrow, purely mechanical edit is exactly the case most likely to skip the surrounding docstrings/comments by default; treat it as license to do the opposite, not an exemption. This applies in particular to the `_build_date_range`/`_build_date_ranges` helper: whenever that routine is touched, check its docstring's NOTE is present, accurate, and lists the routine among its siblings — for the "always both, no gate" group see the NOTE already in `bot_002.py` (identical in all 12 members, sibling list included — update it in every member if the group changes); for a routine with its own no-gate window see `bot_103.py`/`bot_441.py`, which point back at the group instead of listing it. `bot_441.py` is the reference for overall comment/docstring density (the `run()`-race *why* comment, the `finally`-block reasoning comments) — if a bot you're revisiting reads thinner than that on the parts CLAUDE.md calls out, bring it up to the same level in the same pass.
- Every convention above is applied **opportunistically, one routine at a time**, whenever that specific `bot_XXX.py` is next touched — never as a bulk find-and-replace across every routine at once, even when a repo-wide gap is found (exceptions: the one-off full-repo sweep of 2026-09-21 brought every bot, workflow and `app/core` file up to these conventions; on 2026-09-27 the multi-range routines 002–411 were converted to the split shape of step 4 in one pass, at the user's request).

## Log messages

Every `log.log(...)` message that reaches the file is **one line, simple and objective**: `<DOMAIN> <TAG> <routine> | <file/range> | <fact>`. The domain log is uploaded to SharePoint and read by people checking many companies — the user trims anything verbose. Concretely:
- No file paths when the source is obvious, no restating the rule that was broken — just the fact and what to fix.
- No library tracebacks or multi-line dumps (Pydantic `ValidationError`, Playwright's `=== logs ===` block): reduce them to a single line before logging.
- Prefer showing positions as Excel column letters when a person will look them up in the file.
- **SharePoint paths in the log carry the tenant prefix** (`'softweb268/Banco_de_Dados/TEC/441'`, from `_tenant()` in `upload_to_sharepoint.py` = first label of `SHAREPOINT_DOMAIN_*`), for report and log uploads alike, since company copies may point at different tenants.
- **Block order per item (user decision, 2026-09-29), for every routine incl. 930:** `Login successful.` → `Start Dates: [start → end]` (logged by the loop in `handler_download` before each item; `Start Date: [ddmmyy]` for the per-day 458/930; once per range in 076, before its units; once in 571's `check_and_download_report`) → the `upload_report_file` line → the `SENTINEL` line, which closes the block. No trailing "Download and upload successful" / "Processed via screen 156" line — the upload line already confirms it. Routines without a date filter (019, 081, 435) have no `Dates` line; their file name carries the unit/option. The file is newest-first, so a block reads bottom-up.

## Column Sentinel

Guards the **column layout** of SSW files (name, position, count) so an SSW change is noticed before it breaks Power BI. Full doc: [1DOCS/column_sentinel.md](1DOCS/column_sentinel.md). Code: `app/core/column_sentinel.py`. Baselines: `config/sentinel/<name>.yml`.

### Premises (decided with the user — do not revisit unless asked)

1. **Runs after processing and after the upload.** The check happens after the routine's `Dataframe_Analytics` step (if any) and right after `upload_report_file` (user decision, 2026-09-29), so each file's block in the log reads dates → upload → sentinel and the sentinel line closes it. If the upload raises, that file isn't checked. The baseline describes the **processed** file — the layout Power BI expects. Known SSW renames are absorbed in `data_processing.py`; a new legitimate SSW change is fixed there (routines with processing) or in the YAML (raw-file routines) — **never in the sentinel code**.
2. **Never blocks.** The report is still processed and uploaded to SharePoint exactly as before; the log is uploaded as always (`<DOMAIN>.log`). The sentinel only adds **one line per checked file** to the domain log and never raises.
3. **Per company, not generalized.** Companies differ slightly, so each company's project copy owns its `config/sentinel/*.yml`. There is no shared/global baseline and no per-domain override mechanism — edit the YAML in that company's copy.
4. **The YAML is the fixed contract.** `position: 'exact name'`, **1-based** (= Excel column number, 1 = A), strict 1..N numbering with no gaps, no empty/duplicated names. Leading/trailing spaces are ignored; case is compared. The empty last column from SSW's trailing `;` is ignored (never listed). `'1'` (SSW row counter) is a real column at position 1 when the delivered file has it. `header_line` is optional — when omitted, the header is auto-detected in the first 10 lines. `label_separator` is optional — header cells shaped `label: value` are compared by the label only (added on 2026-10-20 for 916's totals).
5. **Routines whose processing selects columns (103, 441, 455CGK):** the baseline must be identical to `config.yml → variables.bot_XXX.columns_XXX` (same names, same order, including a leading `'1'`); changing one means changing the other.
6. **Log format** (see "Log messages"):
   ```
   SNTL - RPV SENTINEL 002 | 2026-09-RPV-002.csv | Columns: 40 found / 40 expected | OK
   WARN - RPV SENTINEL 002 | 2026-09-RPV-002.csv | Columns: 41 found / 40 expected (+1) | DRIFT | ADDED: [G] 'NOVA'
   ERRO - TEC SENTINEL 455ABH | 2026-08-TEC-455ABH.csv | ERROR | missing position(s) [K] [11] 'Placa de Coleta'
   ```
   DRIFT details: `ADDED` / `REMOVED` / `RENAMED` / `MOVED` / `DUPLICATED`, with Excel letters. One inserted column is one `ADDED` (the columns after it are not reported as moved). A removal + addition at the same spot is reported as `RENAMED`. For a baseline with a numbering gap, the missing column's name is taken from the downloaded file's header.

### Guarded routines

| Name (YAML) | Cols | Header | Processing before check | Hook |
|---|---|---|---|---|
| `002` | 40 | auto | none (raw) | `bot_002.py`, both download paths |
| `019` | 30 | auto | none (raw) | `bot_019.py` direct + `downloads.py` (156) |
| `036` | 47 | auto | none (raw) | `bot_036.py` |
| `073-FROTA` / `073-TERCEIROS` | 56 / 79 | auto | none (raw) | `bot_073.py`, one check per csv extracted from the zip (guarded by `has_baseline`; the non-zip `073` fallback has no baseline) |
| `076` | 29 | auto | none (raw) | `bot_076.py` direct + `downloads.py` (156) |
| `081` | 47 | auto | none (raw) | `bot_081.py` |
| `103` | 47 | line 1 | `processing_csv_103` (select/order) — dormant | `bot_103.py` |
| `119` | 7 | line 1 (`.xlsx`) | `processing_sswweb_119` (`.sswweb` → `.xlsx`) — dormant | `bot_119.py` |
| `200` | 70 | auto | none (raw) | `bot_200.py`, both download paths |
| `306` | 14 | auto | none (raw) | `bot_306.py` direct + after prompt + `downloads.py` (156) |
| `322` | 17 | auto | none (raw) | `bot_322.py`, one check per year |
| `324` | 7 | line 1 (`.xlsx`) | `processing_sswweb_324` (`.sswweb` → `.xlsx`) — dormant | `downloads.py` (156) |
| `411` | 29 | auto | none (raw) | `bot_411.py` |
| `435` | 40 | auto | none (raw) | `bot_435.py`, csv option only |
| `441` | 53 | line 1 | `processing_csv_441` (fill/select/order) — dormant | `bot_441.py` direct + `downloads.py` (156) |
| `442` | 15 | auto | none (raw) | `bot_442.py` |
| `452` | 23 | line 1 | `processing_csv_452` (drops title lines, keeps all columns) | `bot_452.py` |
| `455ABH` | 118 | line 2 | `processing_csv_455ABH` (renames) | `downloads.py` (156) |
| `455CGK` | 31 | line 1 | `processing_csv_455CGK` (select/order) — dormant | `downloads.py` (156) |
| `455F` | 90 | auto | none (raw) | `downloads.py` (156) |
| `458` | 9 | line 1 (`.xlsx`) | none — bot builds the `.xlsx` from scraped rows — dormant | `bot_458.py` |
| `467` | 100 | auto | none (raw) | `downloads.py` (156) |
| `477` | 43 | auto | none (raw) | `bot_477.py` direct + `downloads.py` (156) |
| `493` | 49 | auto | none (raw) | `downloads.py` (156) |
| `916-MANIFESTO` / `916-CTRC` | 20 / 14 | line 2 / line 3 | none (raw) | `downloads.py` (156), both baselines against the same file via `SENTINEL_BASELINES` (two header bands; `916-MANIFESTO` uses `label_separator: ':'` for its `label: value` totals) |
| `930` | 24 | auto | none (raw) | `downloads.py` (156), one check per date |

**Dormant baselines (on purpose):** `103`, `119`, `324`, `441`, `455CGK`, `458` have a baseline and run the check, but their processing forces the output header (fixed list in `config.yml`, or names hard-coded in `data_processing.py` / the bot — `458` scrapes a table into an `.xlsx` with `DETAIL_COLUMNS`), so today the check always logs `OK` and validates nothing. Decided with the user on 2026-09-27: keep them ready for when the fixed-column method is replaced. Each routine doc carries a "⚠️ Dormant today" note in its Column Sentinel section, and `1DOCS/column_sentinel.md` has the "Dormant baselines" table — keep both when adding another such routine. While dormant, a baseline must stay identical to what the processing writes. The sentinel reads CSV and `.xlsx` (first sheet; `delimiter`/`encoding` ignored), which is how 119/324/458 are checked.

**Not guarded:** `571` — a pure `.sswweb` text report uploaded as-is, with no header to compare (user decision, 2026-09-27; recorded in `1DOCS/bot_571.md` and in the "Routines not guarded" table of `1DOCS/column_sentinel.md`). `056` has not been evaluated yet.

### Adding a routine (the user sends the column dict)

1. Create `config/sentinel/<name>.yml` (1-based; drop a trailing empty column; mark `'1'` with `# SSW row counter`; omit `header_line` unless known). If the routine produces several files (like 073), one YAML per file, named as the bot names that file.
2. Hook `check_file(path_download / file_name, <name>, domain)` right **after** the `upload_report_file` of the saved/processed file. Screen-156 routines are already covered by `downloads.py`; only direct-download paths need a hook in the bot. Use `has_baseline(name)` as a guard when the name is dynamic.
3. Test in the scratchpad with synthetic CSVs (OK + at least one DRIFT case) and show the resulting log lines.
4. Update **all** docs: the table in `1DOCS/column_sentinel.md`, the routine's `1DOCS/bot_XXX.md` (Column Sentinel section, flowchart node, Related Files), `1DOCS/app_core.md` if a hook in `app/core` changed, and the table above.
5. Tell the user what to copy / edit in each company's copy.

### Pending decisions (offered to the user, not answered yet)

- Raise `DOWNLOAD_TIMEOUT_MS` in `downloads.py` from 120 s to 300 s (a full-month 455CGK range timed out waiting for the download). Resolved differently on 2026-09-29: the 441 no-download was SSW refusing the "Baixar" click (`ajaxEnvia('DOW<seq>')`, AJAX) with the alert "Ja existe uma requisicao em andamento." while another AJAX call of the 156 page was pending. `_click_and_catch_download()` now clicks only when the page is idle and re-clicks after that alert; once "Baixar" shows, the page is never refreshed again (user rule). `DOWNLOAD_TIMEOUT_MS` is 60 s per click, up to 4 clicks. Only revisit the 300 s idea if a download still times out after that.
- Make `processing_csv_455CGK` skip columns missing from the raw file (keeping `config.yml` order) instead of raising `KeyError`, so the sentinel can report `REMOVED` instead of a generic range failure.
