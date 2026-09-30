# Column Sentinel

Guards the **column layout** of SSW report files. SSW can rename, move, add or
remove columns without notice, and Power BI silently breaks when that happens.
The sentinel compares every downloaded file against a fixed baseline and writes
**one line per file** to the domain log saying whether the layout is intact —
and, if not, exactly what changed.

It never blocks anything: the report is still processed and uploaded to
SharePoint as before. The sentinel only adds information to the log.

---

## How it works

```
download ──► processing (if the routine has one) ──► upload to SharePoint ──► COLUMN CHECK
                                                                                   │
                                                          1 line in output/logs/<DOMAIN>.log ◄─┘
```

1. The routine downloads the file from SSW.
2. If the routine has a processing step (`Dataframe_Analytics`), it runs first.
   Known SSW renames are handled there, so the file already has the layout
   Power BI expects.
3. The sentinel reads **only the header** of the resulting file (the report
   body is never loaded) and compares it with `config/sentinel/<ROUTINE>.yml`.
   This happens **right after the upload**, on the local copy (still on disk
   until the end-of-run cleanup).
4. The verdict goes to the domain log as the last line of that file's block.

Why after processing: the processing step absorbs the SSW changes we already
know about. The sentinel only needs to flag what is still **unknown**.

Why after the upload (user decision, 2026-09-29): each file's block in the
domain log reads in order — dates → upload → sentinel — so the sentinel line
closes it. If the upload raises, the check doesn't run for that file (the
range fails with an `ERROR` line instead).

Log block per date range (the log file is newest-first, so read it bottom-up):

```
SNTL - NBL SENTINEL 306 | 2026-09-NBL-306-1.csv | Columns: 14 found / 14 expected | OK
INFO - NBL ROUTINE 306 | '2026-09-NBL-306-1.csv' uploaded to 'softweb268/Banco_de_Dados/NBL/306' - attempt 1/3.
INFO - NBL ROUTINE 306 | Start Dates: [010926 → 150926]
INFO - NBL ROUTINE 306 | Login successful.
```

---

## Files

| File | Role |
|---|---|
| [`app/core/column_sentinel.py`](../app/core/column_sentinel.py) | The sentinel: baseline loading/validation, header reading, comparison and log formatting. |
| `config/sentinel/<ROUTINE>.yml` | One baseline per file type — the expected columns, by position. |
| [`app/core/downloads.py`](../app/core/downloads.py) | Hook for every routine that downloads through screen 156 (`process_156_report`). |
| `app/routines/bot_XXX.py` | Hook for routines that download directly (without screen 156). |

A routine **without** a baseline file is simply not checked — no error, no log line.

---

## Guarded routines

| Routine | Baseline | Columns | Header line | Processing before check | Where the check runs |
|---|---|---|---|---|---|
| 002 | `002.yml` | 40 | auto-detect | none (raw file) | `bot_002.py` — both download paths |
| 019 | `019.yml` | 30 | auto-detect | none (raw file) | `bot_019.py` (direct) + `downloads.py` (156) |
| 036 | `036.yml` | 47 | auto-detect | none (raw file) | `bot_036.py` |
| 073 | `073-FROTA.yml` | 56 | auto-detect | none (raw file) | `bot_073.py` — each csv extracted from the zip |
| 073 | `073-TERCEIROS.yml` | 79 | auto-detect | none (raw file) | `bot_073.py` — each csv extracted from the zip |
| 076 | `076.yml` | 29 | auto-detect | none (raw file) | `bot_076.py` (direct) + `downloads.py` (156) |
| 081 | `081.yml` | 47 | auto-detect | none (raw file) | `bot_081.py` |
| 103 | `103.yml` | 47 | 1 | `processing_csv_103` (selects/orders columns) — dormant | `bot_103.py` |
| 119 | `119.yml` | 7 | 1 (`.xlsx`) | `processing_sswweb_119` (`.sswweb` → `.xlsx`) — dormant | `bot_119.py` |
| 200 | `200.yml` | 70 | auto-detect | none (raw file) | `bot_200.py` — both download paths |
| 306 | `306.yml` | 14 | auto-detect | none (raw file) | `bot_306.py` (direct + after prompt) + `downloads.py` (156) |
| 322 | `322.yml` | 17 | auto-detect | none (raw file) | `bot_322.py` — one check per year |
| 324 | `324.yml` | 7 | 1 (`.xlsx`) | `processing_sswweb_324` (`.sswweb` → `.xlsx`) — dormant | `downloads.py` (156) |
| 411 | `411.yml` | 29 | auto-detect | none (raw file) | `bot_411.py` |
| 435 | `435.yml` | 40 | auto-detect | none (raw file) | `bot_435.py` — csv option only (the txt has no header) |
| 441 | `441.yml` | 53 | 1 | `processing_csv_441` (fills/selects/orders columns) — dormant | `bot_441.py` (direct) + `downloads.py` (156) |
| 442 | `442.yml` | 15 | auto-detect | none (raw file) | `bot_442.py` |
| 452 | `452.yml` | 23 | 1 | `processing_csv_452` (drops title lines, keeps all columns) | `bot_452.py` |
| 455ABH | `455ABH.yml` | 118 | 2 | `processing_csv_455ABH` (renames) | `downloads.py` (156) |
| 455CGK | `455CGK.yml` | 31 | 1 | `processing_csv_455CGK` (selects/orders columns) — dormant | `downloads.py` (156) |
| 455F | `455F.yml` | 90 | auto-detect | none (raw file) | `downloads.py` (156) |
| 458 | `458.yml` | 9 | 1 (`.xlsx`) | none — the bot builds the `.xlsx` from scraped rows — dormant | `bot_458.py` |
| 467 | `467.yml` | 100 | auto-detect | none (raw file) | `downloads.py` (156) |
| 477 | `477.yml` | 43 | auto-detect | none (raw file) | `bot_477.py` (direct) + `downloads.py` (156) |
| 493 | `493.yml` | 49 | auto-detect | none (raw file) | `downloads.py` (156) |
| 930 | `930.yml` | 24 | auto-detect | none (raw file) | `downloads.py` (156) — one check per date |

Notes:

- **073** downloads a `.zip` with two csv files. The bot names them `073-FROTA`
  and `073-TERCEIROS` by the `frota`/`terceiro` suffix inside the zip, and each
  one is checked against the baseline with that same name. The single-csv
  fallback (no zip) is named `073` and has no baseline, so it is not checked.
- **019**, **076** and **081** produce one file per unit (076: per unit and month), so
  they write one sentinel line per file. 076 alone can write ~70 lines per run.
- **455CGK** header is on line 1 because its processing drops the SSW title line.
- **103** header is on line 1 because its processing rewrites the file with only
  `columns_103`; that list starts with the SSW row counter `'1'`, so the baseline does too.

### Dormant baselines

Some routines **have** a baseline and run the check, but their processing
forces the output header, so the check cannot catch an SSW change today —
it always logs `OK`. The baselines are kept ready on purpose (decided with the
user on 2026-09-27): if the fixed-column method is replaced, they start
catching real changes with no code change.

| Routine | Why it validates nothing today | Becomes useful when |
|---|---|---|
| 103 | `processing_csv_103` writes exactly `columns_103`; a missing column fails earlier with `KeyError`, extras are dropped | `columns_103` stops being the output contract |
| 119 | `.sswweb` text sliced by fixed positions into an `.xlsx`; the 7 names are hard-coded in `processing_sswweb_119` | the processing stops hard-coding the names |
| 324 | `.sswweb` text parsed by regex into an `.xlsx`; the 7 names are hard-coded in `processing_sswweb_324` | the processing stops hard-coding the names |
| 441 | `processing_csv_441` **fills missing columns with blanks**, drops extras and reorders to `columns_441` — a removed SSW column reaches Power BI as an empty column, silently (tested: raw file without `SERASA` → `OK`) | `columns_441` stops being the output contract |
| 455CGK | `processing_csv_455CGK` writes exactly `columns_455CGK`; a missing column fails earlier with `KeyError`, extras are dropped | `columns_455CGK` stops being the output contract |
| 458 | No SSW file: the bot scrapes the table and writes the `.xlsx` with `DETAIL_COLUMNS` hard-coded, mapping cells by position — an inserted SSW column shifts values silently (tested) | the header is read from the SSW table's row 1 |

While dormant, keep each baseline identical to the list or names the
processing uses. Each routine's doc carries a "⚠️ Dormant today" note in its
Column Sentinel section.

### Routines not guarded

No baseline at all — there is nothing with a column header to compare:

| Routine | Why not |
|---|---|
| 571 | SSW delivers a pure `.sswweb` text report, uploaded as-is (no processing, no `.csv`/`.xlsx`). Decided with the user on 2026-09-27: no sentinel for now. |
| 056 | Not evaluated yet (no column list received). It also downloads `.sswweb` files, converted to `.xlsx` by `processing_sswweb_056`. |

---|---|---|
| 119 | `.sswweb` text report sliced by fixed character positions into an `.xlsx`; the 7 column names are hard-coded in `processing_sswweb_119`, so the header never changes. The real risk (shifted slices) is invisible to a header check. | A check inside the processing (0 rows extracted, or the report's own title line at the expected positions) |
| 324 | `.sswweb` text report parsed by regex into an `.xlsx`; the 7 column names are hard-coded in `processing_sswweb_324`, so the header never changes. | A `WARNING` in the processing when 0 rows are extracted |

Rule of thumb: the sentinel only makes sense when the **header comes from SSW**
(raw CSV, or processing that selects/renames SSW columns). If the processing
invents the header, there is nothing to guard at that level.

---

## Baseline format (`config/sentinel/<ROUTINE>.yml`)

```yaml
routine: '036'
# header_line omitted: the header is auto-detected in the first lines
delimiter: ';'
encoding: 'windows-1252'
columns:
  1: '1'                    # SSW row counter
  2: 'ROMANEIO'
  3: 'DATA EMISSAO'
  ...
  47: 'PREV ENTREGA CTRC'
```

| Key | Meaning |
|---|---|
| `routine` | Routine/file name. Must match the file name (`036.yml` → `'036'`). |
| `header_line` | Optional. 1-based line that holds the header. When omitted, the sentinel picks the line (within the first 10) sharing the most names with the baseline — a title line above the header needs no config. |
| `delimiter` | CSV separator. SSW uses `;`. Ignored for `.xlsx` files (119/324/458), which are read from the first sheet. |
| `encoding` | File encoding. SSW uses `windows-1252`. Ignored for `.xlsx` files. |
| `columns` | `position: 'exact name'`, **1-based**, same numbering as Excel (1 = A). |

Rules — the baseline is validated when loaded; breaking one logs an `ERROR`
instead of comparing against a broken contract:

- Positions run **1..N with no gaps** (removing a line means renumbering those below it).
- No empty names, no duplicated names.
- Leading/trailing spaces in names are ignored (`'DESC NTC '` = `'DESC NTC'`).
  Upper/lower case **is** compared.
- The empty last column produced by SSW's trailing `;` is ignored — do not list it.
- `'1'` is the SSW row counter present in some reports; when the delivered file
  has it, it is a real column at position 1.

---

## Reading the log

One line per checked file, in the domain log (`output/logs/<DOMAIN>.log`):

```
<date time> - <LEVEL> - <DOMAIN> SENTINEL <routine> | <file> | Columns: <found> found / <expected> expected [(±n)] | <STATUS> [| details]
```

### Status

| Level | Status | Meaning |
|---|---|---|
| `SNTL` | `OK` | Layout identical to the baseline. `SNTL` is a dedicated level (25, between INFO and WARN) so the line stands out. |
| `WARNING` | `DRIFT` | Something changed — details follow on the same line. |
| `ERROR` | `ERROR` | The check could not run (unreadable file, header not found, broken baseline). |

### Details (DRIFT)

Positions are shown as **Excel column letters**, so you can jump straight to the column.

| Tag | Example | Meaning | Letter refers to |
|---|---|---|---|
| `ADDED` | `ADDED: [F] 'NOVA'` | Column in the file that the baseline doesn't have. | downloaded file |
| `REMOVED` | `REMOVED: [K] 'AUTORIZACAO'` | Baseline column missing from the file. | baseline |
| `RENAMED` | `RENAMED: [AN] 'Base de Calculo' -> 'Base Calculo ICMS/ISS'` | Same spot, different name (`baseline -> file`). | downloaded file |
| `MOVED` | `MOVED: 'CUBAGEM' U -> W` | Column changed its relative order (`baseline -> file`). | both |
| `DUPLICATED` | `DUPLICATED: 'CTRC'` | The same name appears more than once in the file. | — |

How changes are classified:

- **One inserted column is one `ADDED`.** The columns after it shift letters but
  keep their order, so they are *not* reported as moved (otherwise a single new
  column would produce dozens of alerts).
- **A swap reports one `MOVED`** — the single column that explains the change.
- **Combined changes are split**: e.g. a column moved *and* a new one inserted
  gives `ADDED: [V] 'NOVA' | MOVED: 'CUBAGEM' U -> W`.
- **Ambiguity:** a column removed and a new one added at the very same spot is
  reported as `RENAMED`. From the header alone the two are indistinguishable;
  either way the line shows position, old name and new name.

### Examples

```
SNTL - RPV SENTINEL 455CGK | 2026-08-RPV-455CGK.csv | Columns: 31 found / 31 expected | OK
WARN - RPV SENTINEL 002 | 2026-09-RPV-002.csv | Columns: 41 found / 40 expected (+1) | DRIFT | ADDED: [G] 'NOVA'
WARN - TEC SENTINEL 036 | 2026-09-TEC-036.csv | Columns: 46 found / 47 expected (-1) | DRIFT | REMOVED: [K] 'AUTORIZACAO'
WARN - RPV SENTINEL 019 | RPV-SMD-019.csv | Columns: 30 found / 30 expected | DRIFT | RENAMED: [O] 'SET' -> 'SETOR'
WARN - TEC SENTINEL 076 | 2026-09-TEC-RIC-076.csv | Columns: 29 found / 29 expected | DRIFT | MOVED: 'SET' U -> AC
```

### Errors

```
ERRO - TEC SENTINEL 455ABH | 2026-08-TEC-455ABH.csv | ERROR | missing position(s) [K] [11] 'Placa de Coleta'
ERRO - RPV SENTINEL 002 | 2026-09-RPV-002.csv | ERROR | duplicated column name(s) ['VENDEDOR']
ERRO - RPV SENTINEL 002 | 2026-09-RPV-002.csv | ERROR | empty column name
ERRO - RPV SENTINEL 002 | no_header.csv | ERROR | No header found in the first 10 lines: no_header.csv
```

- `missing position(s)`: the baseline numbering has a gap. The column name is
  taken from the downloaded file's header (the line no longer exists in the YAML).
  Fix: renumber the YAML.
- `No header found`: none of the first 10 lines shares a name with the baseline —
  wrong file, wrong encoding, or a completely different layout.

Errors are always a single line; library tracebacks are never written to the log.

---

## Common tasks

### SSW changed a column and the change is legitimate

- **Routine with processing (103, 455ABH, 455CGK):** adjust the processing in
  `app/core/data_processing.py` (and, for 103/455CGK, `columns_103`/`columns_455CGK` in
  `config/settings/config.yml`) so the output matches what Power BI expects.
  The baseline stays the same.
- **Raw-file routine (002, 019, 036, 073, 076, 081, 200, 306, 322, 411, 435, 442, 455F, 467, 477, 493, 930 — and 452, whose processing keeps every column):** update the baseline YAML
  (name, position or new line) and renumber if needed.

Rule of thumb: **the baseline must equal the file that leaves processing** —
never change the code to accept a new layout, only the YAML/processing.

### A company uses a different layout

Baselines are **per company**: each company's project copy has its own
`config/sentinel/` folder. Edit the YAML in that copy only. For 103 and 455CGK, keep
`config.yml → columns_103`/`columns_455CGK` and `103.yml`/`455CGK.yml` identical
(same names, same order).

### Add a new routine

1. Create `config/sentinel/<ROUTINE>.yml` with the columns (1-based).
2. Hook the check right **after the upload** of the saved/processed file:
   - Routine downloads through screen 156 → nothing to do, `downloads.py` already checks
     every routine that has a baseline.
   - Routine downloads directly → in `bot_<ROUTINE>.py`:
     ```python
     from app.core.column_sentinel import check_file

     await download.save_as(path_download / file_name)
     await upload_report_file(...)
     check_file(path_download / file_name, ROUTINE, domain)
     ```
     When the file name is not the routine (e.g. 073), guard with
     `has_baseline(name)` and pass that name.
3. Run the routine and check the log for `Columns: N found / N expected | OK`.

### Test a false positive

Change one column name in the YAML (e.g. `7: 'VENDEDOR X'`), run the routine and
expect a `RENAMED` line. Other tests: remove a line **and renumber** (→ `ADDED`),
add a fake last column (→ `REMOVED`), swap two names keeping the numbers
(→ `MOVED`). Whitespace-only changes are not detected. **Revert the YAML afterwards.**

---

## Known limitations

- **Dormant baselines (103, 119, 324, 441, 455CGK, 458):** see "Dormant baselines" above.
- **103 and 455CGK processing fails before the check** if SSW removes or renames one of
  the selected columns (47 / 31): `to_csv(columns=...)` raises `KeyError` and the range
  fails with a generic error. Columns added or moved in the raw file don't affect
  the output (they are dropped/reordered by the processing), so they are not flagged.
- **Log size:** the sentinel writes one line per file. With a small log cap
  (e.g. 100 lines) routines like 076 can push older entries out in a single run.
  This project's logger keeps 1000 lines and is cleared at 01h on weekdays
  (`wf_daily.py`).
- Only the header is checked — data types and values are out of scope.

---

## Porting to another project copy

Copy:

- `app/core/column_sentinel.py`
- `config/sentinel/*.yml` (then review each one against that company's layout)

Edit by hand (don't overwrite — the target file may have company-specific code):

- `app/core/downloads.py` — import `check_file, has_baseline` and, in
  `process_156_report`, right after the `upload_report_file(...)` that follows
  `file_name = await handler(...)`:
  ```python
  if has_baseline(routine):
      check_file(path_download / file_name, routine, domain)
  ```
- `bot_002.py`, `bot_019.py`, `bot_036.py`, `bot_076.py`, `bot_081.py`, `bot_200.py`, `bot_306.py`, `bot_322.py`, `bot_411.py`, `bot_435.py`, `bot_442.py`, `bot_477.py` — import `check_file`
  and add `check_file(path_download / file_name, ROUTINE, domain)` right after
  each `await upload_report_file(...)` that follows a `download.save_as(...)`.
- `bot_103.py` — import `check_file` and add `check_file(full_file_path, ROUTINE, domain)`
  after `processing_csv_103` and right after `upload_report_file`.
- `bot_452.py` — import `check_file` and add `check_file(full_file_path, ROUTINE, domain)`
  after `processing_csv_452` and right after `upload_report_file`.
- `bot_458.py` — import `check_file` and add `check_file(full_file_path, ROUTINE, domain)`
  after the `.xlsx` is written and right after `upload_report_file`.
- `bot_119.py` — import `check_file` and add `check_file(path_download / new_file_name, ROUTINE, domain)`
  after `processing_sswweb_119` and right after `upload_report_file`.
- `bot_441.py` — import `check_file` and add `check_file(path_download / final_file_name, ROUTINE, domain)`
  after `processing_csv_441` and right after `upload_report_file`.
- `bot_073.py` — import `check_file, has_baseline` and add, right after the
  `upload_report_file(...)` in `_extract_and_upload_zip` and in `_save_and_upload`:
  ```python
  if has_baseline(routine_name):
      check_file(path_download / file_name, routine_name, domain)
  ```

No new dependencies (`pydantic`, `pyyaml` are already in `pyproject.toml`).
Requires Python 3.11+ (`StrEnum`). Verify with:

```bash
uv run python -m py_compile app/core/column_sentinel.py app/core/downloads.py
uv run python main.py 455CGK
```
