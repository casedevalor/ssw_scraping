# 🧠 Softweb - SSW Reports Automation Suite

Automations that extract reports from the **SSW** transport-management system, built with **modern Python (asyncio + Playwright)**. Each routine logs into the web system, runs a numbered report ("rotina") according to its business parameters, downloads (and, when needed, processes) the resulting file, checks its column layout, and uploads it to **SharePoint** via the Graph API.

**This folder is the standard project ("pasta padrão")** — the reference version since 2026-09-27. Each company runs its own copy, pointed at its SSW domain through `SSW_DOMAIN` in `config/credentials/.env` (e.g. `AL2`, `IVC`, `AGE`). Changes are made here first and then carried by hand to each company's copy, which may carry company-specific code or baselines.

---

## 📋 Routines

| Routine | Report | Period | How the file arrives | Output | Column Sentinel | Scheduled in |
| --- | --- | --- | --- | --- | --- | --- |
| [002](./1DOCS/bot_002.md) | Quotations | Previous + current month | Direct (or after "report generated" prompt) | `.csv` | ✅ | WF_ALL |
| [019](./1DOCS/bot_019.md) | Pending deliveries (CTRCs), per unit | No date filter (per unit) | Direct or screen 156 | `.csv` per unit | ✅ | — |
| [036](./1DOCS/bot_036.md) | Waybills (romaneios) | Previous + current month | Direct | `.csv` | ✅ | WF_DAILY, WF_ALL |
| [056](./1DOCS/bot_056.md) | `.sswweb` reports by code | No date filter (per report code) | Direct | `.xlsx` (from `.sswweb`) | not evaluated | — |
| [073](./1DOCS/bot_073.md) | Vehicles (fleet / third-party) | Previous + current month | Direct (`.zip` with 2 csv) | 2 × `.csv` | ✅ (one baseline per csv) | — |
| [076](./1DOCS/bot_076.md) | Delivery write-offs (baixas), per unit | Previous + current month × units | Direct or screen 156 | `.csv` per unit and month | ✅ | — |
| [081](./1DOCS/bot_081.md) | Deliveries, per unit | No date filter (per unit) | Direct | `.csv` per unit | ✅ | WF_ALL |
| [103](./1DOCS/bot_103.md) | Pickups | Last 3 months | Direct | `.csv` (processed) | 💤 dormant | WF_DAILY, WF_ALL |
| [119](./1DOCS/bot_119.md) | Visit occurrences | Previous + current month | Direct | `.xlsx` (from `.sswweb`) | 💤 dormant | — |
| [200](./1DOCS/bot_200.md) | Manifests | Previous + current month | Direct (or after prompt) | `.csv` | ✅ | WF_ALL |
| [306](./1DOCS/bot_306.md) | Vehicle pickups/deliveries, per fortnight | Previous + current fortnight | Direct, after prompt, or screen 156 | `.csv` per fortnight | ✅ | WF_ALL |
| [322](./1DOCS/bot_322.md) | Fuel supply | Previous year + current year to date | Direct (or after prompt) | `.csv` per year | ✅ | — |
| [324](./1DOCS/bot_324.md) | Vehicle income/expenses | Previous + current month | Screen 156 | `.xlsx` (from `.sswweb`) | 💤 dormant | — |
| [411](./1DOCS/bot_411.md) | Liquidations | Previous + current month | Direct | `.csv` | ✅ | WF_ALL |
| [435](./1DOCS/bot_435.md) | CTRCs (two formats) | No date filter (csv + txt options) | Direct | `.csv` + `.txt` | ✅ (csv only) | WF_ALL |
| [441](./1DOCS/bot_441.md) | Invoices | Last 3 years (one range per year) | Direct or screen 156 | `.csv` per year (processed) | 💤 dormant | WF_ALL |
| [442](./1DOCS/bot_442.md) | Credits/debits | Previous + current month | Direct | `.csv` | ✅ | — |
| [452](./1DOCS/bot_452.md) | Liquidated freight | Previous + current month | Direct | `.csv` (title lines dropped) | ✅ | — |
| [455ABH](./1DOCS/bot_455ABH.md) | 455, variant ABH | Previous + current month | Screen 156 | `.csv` (processed) | ✅ | WF_DAILY, WF_ALL, WF_WE (Sat) |
| [455CGK](./1DOCS/bot_455CGK.md) | 455, variant CGK | Previous + current month | Screen 156 | `.csv` (processed) | 💤 dormant | WF_DAILY, WF_ALL, WF_WE (Sun) |
| [455F](./1DOCS/bot_455F.md) | 455, variant F | Previous + current month | Screen 156 | `.csv` | ✅ | — (disabled in WF_WE; enable per domain) |
| [458](./1DOCS/bot_458.md) | Bank-balance snapshot | Yesterday + today (one search per day) | Scraped from the screen (no file) | `.xlsx` per day | 💤 dormant | — (enable per domain) |
| [467](./1DOCS/bot_467.md) | Clients | Previous + current month | Screen 156 | `.csv` | ✅ | WF_ALL |
| [477](./1DOCS/bot_477.md) | Expenses | Last 3 years (one range per year) | Direct (after prompt) or screen 156 | `.csv` per year | ✅ | WF_ALL |
| [493](./1DOCS/bot_493.md) | Freight tables | Previous + current month | Screen 156 | `.csv` | ✅ | WF_ALL |
| [571](./1DOCS/bot_571.md) | Pending items | Current year to date | Direct | `.sswweb` (as-is) | — not guarded | WF_ALL |
| [930](./1DOCS/bot_930.md) | Occurrences | Yesterday + today (one run per day) | Screen 156 | `.csv` per day | ✅ | WF_DAILY (every block), WF_ALL |

**Column Sentinel legend:** ✅ real check of the SSW layout · 💤 dormant — the baseline exists and runs, but the processing (or the bot) forces the output header, so today it always logs `OK` · — not guarded / not evaluated. Details in [column_sentinel.md](./1DOCS/column_sentinel.md).

---

## 📚 Reference Documentation (not routines)

- [Column Sentinel — `app/core/column_sentinel.py`](./1DOCS/column_sentinel.md) — guards the column layout of SSW files (name, position, count) against `config/sentinel/<ROUTINE>.yml` and logs one line per file: guarded, dormant and not-guarded routines, baseline format, how to read the log, how to add a routine, how to port it to a company copy.
- [`app/core/`](./1DOCS/app_core.md) — `login.py`, `upload_to_sharepoint.py`, `downloads.py` (screen-156 flow), `data_processing.py` and `column_sentinel.py` (in Portuguese).
- [`Dates()` — `app/utils/datetime_utils.py`](./1DOCS/datetime_utils.md) — every date-calculation method: what it returns, who calls it, and which ones are unused (in Portuguese).

Legacy docs with no matching bot (kept for history, not maintained): [bot_704.md](./1DOCS/bot_704.md), [bot_930_r.md](./1DOCS/bot_930_r.md).

---

## 🧩 How a routine works

Every `app/routines/bot_XXX.py` follows the same shape (full rules in `CLAUDE.md`):

1. **`run()`** opens Chromium, logs in (`perform_login`) and opens the routine's popup, racing it against the menu error label — a routine that is `"Opção não liberada"` for the domain never opens a popup.
2. **`handler_download()`** loops over the routine's items (date ranges, dates, units or options). Each item is handled by one function — `_process_date_range`, `_process_date`, `_process_unit_range` or `_process_option` — so a failure in one item is logged as one `ERROR` line and the next item still runs. Only when **every** item failed is the error re-raised.
3. Inside each item, the form is filled and the outcome is **raced**: file download vs. SSW message (with a 60 s timeout). Screens that queue the report go through **screen 156** (`app/core/downloads.py → process_156_report`), which waits for it (up to 120 refreshes × 4 s), downloads, processes and uploads it.
4. Each item first logs its dates (`Start Dates: [...]`, or `Start Date: [...]` for per-day routines); right after every upload, the **Column Sentinel** checks the file's header and writes one line to the domain log, closing that file's block. It never blocks or undoes the upload.
5. **`main()`** retries `run()` up to 3 times (5 s apart) and stops early on `"Opção não liberada"`. At the end it always uploads the domain log and sends `output/downloads/<ROUTINE>/` to the trash.

---

## 📦 Installation

Dependencies are managed with [`uv`](https://docs.astral.sh/uv/) — do not use bare `pip`/`venv`.

```bash
uv sync
uv run playwright install chromium   # first-time browser install
```

Every command below runs through `uv run`, which resolves the project's environment automatically — no manual activation step. Credentials go in `config/credentials/.env` (never versioned).

---

## ▶️ Running Routines

`main.py` is the only entrypoint. It accepts one or more identifiers, for individual routines or grouped workflows.

### 1. Specific routines

```bash
uv run python main.py 002
uv run python main.py 076 930 455ABH
```

- Routines run sequentially in the same process; each module is imported dynamically and its `main()` awaited.
- A failure in one routine is caught and logged; the batch continues with the next one.

### 2. Workflows (what cron runs)

```bash
uv run python main.py WF_DAILY
uv run python main.py WF_ALL
uv run python main.py WF_WE
```

Production runs come from `scripts/scheduler.sh` via cron. The workflow module decides what to run from the current time:

| Workflow | File | What it runs |
| --- | --- | --- |
| `WF_DAILY` | `app/routines/wf_daily.py` | **01h:** 930, 455ABH, 455CGK (and clears the domain log Mon–Fri) · **12h:** 930, 455ABH, 455CGK, 103, 036 · **any other hour:** 930, 103, 036 |
| `WF_ALL` | `app/routines/wf_startall.py` | 002, 036, 081, 103, 200, 306, 411, 435, 441, 455ABH, 455CGK, 467, 477, 493, 571, 930 |
| `WF_WE` | `app/routines/wf_weekend.py` | **Saturday:** 455ABH (455F commented out — enable per domain) · **Sunday:** 455CGK |

Routines not in any workflow (019, 056, 073, 076, 119, 322, 324, 442, 452, 455F, 458) run only when called by ID, or once a company's copy adds them to a workflow.

---

## 📝 Logs

- `output/logs/<DOMAIN>.log` — one line per event, newest first, capped at 1000 lines; uploaded to SharePoint at the end of every routine. Format: `<DOMAIN> <TAG> | <routine> | <file/range> | <fact>`.
- Column Sentinel lines look like:

  ```text
  SNTL - TEC SENTINEL 002 | 2026-09-TEC-002.csv | Columns: 40 found / 40 expected | OK
  WARN - TEC SENTINEL 002 | 2026-09-TEC-002.csv | Columns: 41 found / 40 expected (+1) | DRIFT | ADDED: [G] 'NOVA'
  ```

- Downloads land in `output/downloads/<ROUTINE>/` and are sent to the OS trash at the end of each run.

---

## ➕ Adding or changing a routine

1. Create/edit `app/routines/bot_XXX.py` following the shape above (`CLAUDE.md` has the full checklist; `bot_322.py` is the multi-range reference). Check that `ROUTINE = "XXX"` matches the file name.
2. Register it in `main.py`'s `ROUTINE_MAP` (and in a `wf_*.py` block if it should be scheduled). If it can go through screen 156, add its `_handle_XXX` in `app/core/downloads.py` (kept in ascending routine order).
3. If SSW sends a file with a column header, add `config/sentinel/XXX.yml` (see [column_sentinel.md](./1DOCS/column_sentinel.md)).
4. Validate with `uv run python -m py_compile app/routines/bot_XXX.py` plus a headed run — there is no automated test suite.
5. Create/update `1DOCS/bot_XXX.md` and its row in the table above.
6. Carry the change to each company's copy: copy new files; for existing ones, check the company's version first (it may have specific code).

---

## 🗂️ Project Structure

```bash
SSW_SENTINEL/
├── app/
│   ├── core/                   # login, SharePoint upload, screen-156 flow, data processing, column sentinel
│   ├── integrations/           # External API clients (SharePoint Graph API, reCAPTCHA solving)
│   ├── routines/               # The bots (bot_*.py) and the workflows (wf_*.py)
│   └── utils/                  # Logger, date-range calculators (Dates), trash cleanup
├── 1DOCS/                      # Per-routine docs + reference docs (sentinel, app/core, Dates)
├── config/
│   ├── credentials/.env        # Credentials and SSW_DOMAIN (not versioned)
│   ├── docs/                   # Special configuration notes (special_domain_settings.md)
│   ├── sentinel/               # Column baselines per routine (<ROUTINE>.yml)
│   └── settings/
│       ├── config.yml          # Runtime variables (unit lists, report codes, column lists)
│       └── variables.py        # Pydantic settings — the single settings source (`settings`)
├── output/
│   ├── downloads/              # Temporary report files, per routine
│   └── logs/                   # Per-domain log files
├── scripts/scheduler.sh        # Cron entry that calls main.py with a workflow ID
├── main.py                     # The only entrypoint (ROUTINE_MAP)
├── pyproject.toml / uv.lock    # Dependencies (uv)
├── CLAUDE.md                   # Conventions and decisions for code changes
└── README.md                   # This file
```

> The `config.yml` at the repo root is **not** read by any code — only `config/settings/config.yml` is.
