import asyncio
import logging

from typing import Dict, List
from pathlib import Path

import pandas as pd
from playwright.async_api import Playwright, Page, async_playwright

from config.settings.variables import settings
from app.core.column_sentinel import check_file
from app.core.login import perform_login
from app.core.upload_to_sharepoint import upload_report_file, upload_log_file
from app.utils.cleanup import GarbageCollector
from app.utils.datetime_utils import Dates
from app.utils.custom_logger import get_logger

# Current directory and routine name
cwd: Path = Path.cwd()
log = get_logger()
ROUTINE: str = "458"

# ---------------------------------------------------------------------------
# Selectors
# ---------------------------------------------------------------------------
SEL_DATE_START = '[id="\\31 "]'
SEL_DATE_END = '[id="\\32 "]'
SEL_ROUTINE_INPUT = '[id="\\33 "]'
SEL_BTN_SUBMIT = "►"
# First (and only) result row link — this screen always opens the detail
# popup off row 2 of #tblsr (row 1 is the header); there is no multi-row
# selection here.
SEL_FIRST_ROW_LINK = "#tblsr > tbody > tr:nth-child(2) > td:nth-child(1) > div > a"
SEL_DETAIL_ROW_TEMPLATE = "#tblsr > tbody > tr:nth-child({row})"
SEL_ERROR_LABEL = "#errormsglabel"
SEL_BTN_OK = "7. OK"

# ---------------------------------------------------------------------------
# Known business messages
# ---------------------------------------------------------------------------
MSG_NOT_RELEASED = "Opção não liberada"

# ---------------------------------------------------------------------------
# Detail table (popup) shape
# ---------------------------------------------------------------------------
# Column order mirrors the SSW screen exactly, left to right.
DETAIL_COLUMNS: List[str] = [
    "Empresa", "Banco", "Agencia", "ContaCorrente",
    "Tipo", "Entradas", "Saidas", "Saldo", "Status",
]
# Generous ceiling on rows probed per detail page — a real page never gets
# this large, it only bounds the row-probe loop below against a runaway scan.
MAX_DETAIL_ROWS_PER_PAGE: int = 120


def _build_dates_to_process() -> List[str]:
    """
    Fixed dates for this routine: yesterday and today, each processed as its
    own single-day search (start = end) — always 2 dates, every run.

    NOTE: no day/hour gate, and not part of the "previous + current month"
    group (bot_002.py and siblings): this is a daily bank-balance snapshot,
    and the user asked on 2026-09-27 for it to cover yesterday + today
    (it used to query today only). Same dates as bot_930.py. Wraps
    Dates().yesterday_and_today() one-to-one.
    """
    return Dates().yesterday_and_today()


async def _get_row_cells(page: Page, row_index: int) -> List[str]:
    """
    Returns the raw textContent of every <td> in one detail-table row, or
    an empty list if the row isn't rendered. textContent (rather than
    inner_text) is used deliberately so cells SSW keeps visually hidden
    still contribute their text.
    """
    row_locator = page.locator(SEL_DETAIL_ROW_TEMPLATE.format(row=row_index))
    try:
        await row_locator.wait_for(state="visible", timeout=1000)
    except Exception:
        return []
    return await row_locator.evaluate(
        "(row) => Array.from(row.querySelectorAll('td')).map(cell => cell.textContent)"
    )


async def _scrape_detail_page(page: Page) -> List[Dict[str, str]]:
    """
    Scrapes every data row (row 2 onward — row 1 is the header) of the
    detail table currently rendered on `page`. Stops at the first row that
    isn't found, or once MAX_DETAIL_ROWS_PER_PAGE is reached.
    """
    rows: List[Dict[str, str]] = []
    for row_index in range(2, MAX_DETAIL_ROWS_PER_PAGE + 2):
        cells = await _get_row_cells(page, row_index)
        if not cells:
            break
        if len(cells) < len(DETAIL_COLUMNS):
            continue
        rows.append(
            {col: cells[i].strip() for i, col in enumerate(DETAIL_COLUMNS)}
        )
    return rows


async def _scrape_all_detail_pages(page: Page, domain: str) -> List[Dict[str, str]]:
    """
    Walks every paginated page of the detail table, scraping each one and
    following the numbered "next page" link until it's no longer present.
    `networkidle` is awaited after each page change so the JS-driven table
    refresh has finished before the next page is scraped.
    """
    all_rows: List[Dict[str, str]] = []
    page_number = 1

    while True:
        log.debug(
            message=f"ROUTINE {ROUTINE} | Scraping detail page {page_number}...",
            level="INFO",
        )
        all_rows.extend(await _scrape_detail_page(page))

        next_page_link = page.get_by_role(
            "link", name=str(page_number + 1), exact=True
        )
        if not await next_page_link.is_visible():
            break

        await next_page_link.click()
        await page.wait_for_load_state("networkidle", timeout=30000)
        page_number += 1

    log.debug(
        message=(
            f"ROUTINE {ROUTINE} | Scraping finished: "
            f"{len(all_rows)} row(s) across {page_number} page(s)."
        ),
        level="INFO",
    )
    return all_rows


async def _open_routine_popup(page: Page) -> Page:
    """
    Opens a fresh routine-458 popup from the menu page. A new popup per date
    keeps each search on a clean screen: reusing one popup would leave the
    previous date's result row visible, so the next race would resolve
    instantly on stale results.

    Raises:
        TimeoutError: When neither the popup nor an error appears within 10 s.
        RuntimeError: When the menu shows an error (e.g. "Opção não liberada").
    """
    await page.locator(SEL_ROUTINE_INPUT).fill(ROUTINE)
    await page.wait_for_timeout(500)

    # Race popup vs. menu-level error label instead of assuming the popup
    # always opens — a routine that is "Opção não liberada" for this
    # domain never opens a popup, only the error label.
    task_popup = asyncio.create_task(page.wait_for_event("popup"))
    task_error = asyncio.create_task(
        page.locator(SEL_ERROR_LABEL).wait_for(state="visible")
    )

    await page.locator(SEL_ROUTINE_INPUT).press("Tab")

    done, pending = await asyncio.wait(
        [task_popup, task_error],
        timeout=10.0,
        return_when=asyncio.FIRST_COMPLETED,
    )

    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)

    if not done:
        raise TimeoutError(
            f"ROUTINE {ROUTINE} | Timeout: neither popup nor error "
            "appeared after pressing Tab."
        )

    if task_error in done and not task_error.exception():
        text = await page.locator(SEL_ERROR_LABEL).inner_text()
        await page.get_by_role("link", name=SEL_BTN_OK).click()
        raise RuntimeError(f"ROUTINE {ROUTINE} | {text}")

    popup: Page = task_popup.result()
    await popup.wait_for_load_state("load")
    return popup


async def _process_date(
    page: Page, domain: str, date: str, attempt: int, max_attempts: int
) -> None:
    """
    Runs one single-day search (start = end = `date`) on a freshly opened
    routine popup. Instead of assuming the results table always populates,
    races the first result row becoming visible against the error label. On
    success, opens the detail popup for that row, scrapes every paginated
    page of it, builds the report as an .xlsx, runs the column sentinel on
    it and uploads it. A message instead of results is logged as WARNING.

    This routine has no browser download event of its own — SSW never
    offers a file here, so the report is built entirely from scraped
    table data instead of being saved from a `download` event.

    Raises:
        TimeoutError: When neither the results row nor a message appears
                      within 60 s.
    """
    start_date, end_date = date, date

    await page.locator(SEL_DATE_START).click()
    await page.locator(SEL_DATE_START).fill(start_date)
    await page.wait_for_timeout(250)
    await page.locator(SEL_DATE_END).fill(end_date)
    await page.wait_for_timeout(250)

    # Listen for both outcomes BEFORE submitting: the results row showing up
    # or a message (e.g. no data) — this screen never fires a download.
    task_row = asyncio.create_task(
        page.locator(SEL_FIRST_ROW_LINK).wait_for(state="visible")
    )
    task_msg = asyncio.create_task(
        page.locator(SEL_ERROR_LABEL).wait_for(state="visible")
    )

    await page.get_by_role("link", name=SEL_BTN_SUBMIT).click()

    done, pending = await asyncio.wait(
        [task_row, task_msg], timeout=60.0, return_when=asyncio.FIRST_COMPLETED
    )
    # Cancel and await the loser so its exception isn't reported as
    # "never retrieved" by asyncio.
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)

    # ------------------------------------------------------------------
    # SCENARIO 1 — Timeout: neither the results row nor a message showed up
    # ------------------------------------------------------------------
    if not done:
        raise TimeoutError("Timeout: neither the results row nor a message appeared.")

    # ------------------------------------------------------------------
    # SCENARIO 2 — A message appeared instead of results (e.g. no data)
    # ------------------------------------------------------------------
    if task_msg in done and not task_msg.exception():
        text = await page.locator(SEL_ERROR_LABEL).inner_text()
        if text:
            await page.get_by_role("link", name=SEL_BTN_OK).click()
        log.log(
            level=logging.WARNING,
            domain=domain,
            message=f"{domain} ROUTINE {ROUTINE} | [{start_date} → {end_date}] {text}",
        )
        return

    # ------------------------------------------------------------------
    # SCENARIO 3 — Results row is there: open its detail popup and scrape it
    # ------------------------------------------------------------------
    async with page.expect_popup() as detail_popup_info:
        await page.locator(SEL_FIRST_ROW_LINK).click()
    detail_page = await detail_popup_info.value
    await detail_page.wait_for_load_state("load")

    rows = await _scrape_all_detail_pages(detail_page, domain)
    await detail_page.close()

    if not rows:
        log.log(
            level=logging.WARNING,
            domain=domain,
            message=(
                f"{domain} ROUTINE {ROUTINE} | [{start_date} → {end_date}] "
                "No rows scraped, nothing to upload."
            ),
        )
        return

    file_name = (
        f"20{start_date[-2:]}-{start_date[2:4]}-{start_date[:2]}"
        f"-{domain}-{ROUTINE}.xlsx"
    )
    path_download = cwd / "output" / "downloads" / ROUTINE
    path_download.mkdir(parents=True, exist_ok=True)
    full_file_path = path_download / file_name

    # Building the DataFrame and writing the .xlsx is sync/CPU-bound work —
    # offloaded to a worker thread so it doesn't block the event loop.
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        None, lambda: pd.DataFrame(rows).to_excel(full_file_path, index=False)
    )
    log.log(
        level=logging.INFO,
        domain=domain,
        message=f"{domain} ROUTINE {ROUTINE} | {len(rows)} row(s) scraped",
    )

    await upload_report_file(
        file_name=file_name,
        routine=ROUTINE,
        domain=domain,
        attempt=attempt,
        max_attempts=max_attempts,
    )
    # The header comes from DETAIL_COLUMNS, so this check is dormant today
    # (see config/sentinel/458.yml). Runs after the upload so its line closes
    # this file's block in the log; never raises.
    check_file(full_file_path, ROUTINE, domain)


async def handler_download(
    page: Page, domain: str, attempt: int, max_attempts: int
) -> None:
    """
    Runs _process_date() once per date from _build_dates_to_process()
    (yesterday + today), each on its own routine popup opened from the menu
    page and closed afterwards.

    Opening the popup stays outside the per-date try: a menu-level failure
    (timeout, "Opção não liberada") would hit every date alike, so it aborts
    the attempt and main() decides whether to retry. Each date's search is
    isolated — a failure in one does not stop the next — but if every date
    fails the error is re-raised so main()'s retry loop still engages.

    Raises:
        RuntimeError: When all dates failed, or the menu shows an error.
        TimeoutError: When the routine popup does not open.
    """
    dates_to_process: List[str] = _build_dates_to_process()
    failures: int = 0

    for date in dates_to_process:
        log.log(
            level=logging.INFO,
            domain=domain,
            message=f"{domain} ROUTINE {ROUTINE} | Start Date: [{date}]",
        )
        popup: Page = await _open_routine_popup(page)
        try:
            await _process_date(popup, domain, date, attempt, max_attempts)
        except Exception as e_date:
            failures += 1
            log.log(
                level=logging.ERROR,
                domain=domain,
                message=f"{domain} ROUTINE {ROUTINE} | Failed to process [{date}]: {e_date}",
            )
        finally:
            # A new popup opens for every date — close it so they don't pile up.
            await popup.close()

    if failures == len(dates_to_process):
        raise RuntimeError(
            f"{domain} ROUTINE {ROUTINE} | All {len(dates_to_process)} date(s) failed."
        )


async def run(playwright: Playwright, attempt: int, max_attempts: int) -> None:
    """
    Launches the browser, logs in and delegates to handler_download(),
    which opens a fresh routine-458 popup per date (racing it against the
    menu-level error label, MSG_NOT_RELEASED).
    """
    browser = None
    context = None

    try:
        browser = await playwright.chromium.launch(
            headless=getattr(settings, "headless", True),
            timeout=30000,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        context = await browser.new_context()
        context.set_default_timeout(120000)
        page = await context.new_page()

        await perform_login(page, ROUTINE)
        await page.wait_for_timeout(1000)

        # The routine popup is opened per date inside handler_download(),
        # so the menu page itself is what gets handed over.
        await handler_download(page, settings.ssw_domain, attempt, max_attempts)

    except Exception as e:
        log.log(
            level=logging.ERROR,
            domain=settings.ssw_domain,
            message=f"{settings.ssw_domain} ROUTINE {ROUTINE} | {e} - attempt {attempt}/{max_attempts}.",
        )
        raise  # plain `raise` preserves the original traceback
    finally:
        if context:
            await context.close()
        if browser:
            await browser.close()


async def main() -> None:
    """
    Initializes Playwright, runs the routine with retry logic,
    and cleans up downloaded files after completion.
    """
    MAX_ATTEMPTS = 3

    try:
        async with async_playwright() as playwright:
            for attempt in range(1, MAX_ATTEMPTS + 1):
                try:
                    await run(playwright, attempt, MAX_ATTEMPTS)
                    break
                except Exception as e:
                    error_msg = str(e)

                    if MSG_NOT_RELEASED in error_msg:
                        log.log(
                            level=logging.WARNING,
                            domain=settings.ssw_domain,
                            message=(
                                f"{settings.ssw_domain} ROUTINE {ROUTINE} | "
                                f"{error_msg}. Aborting attempts."
                            ),
                        )
                        break

                    if attempt == MAX_ATTEMPTS:
                        log.log(
                            level=logging.ERROR,
                            domain=settings.ssw_domain,
                            message=f"{settings.ssw_domain} ROUTINE {ROUTINE} | MAXIMUM ATTEMPTS REACHED. Ending routine.",
                        )
                    else:
                        log.log(
                            level=logging.INFO,
                            domain=settings.ssw_domain,
                            message=f"{settings.ssw_domain} ROUTINE {ROUTINE} | Attempt {attempt} failed. Retrying in 5s...",
                        )
                        await asyncio.sleep(5)
    finally:
        # Runs exactly once, regardless of how many attempts happened or
        # how the routine ended. The "completed" line goes last on purpose:
        # nothing this run logs may come after it.
        domain: str = settings.ssw_domain

        # A cleanup failure is logged, not raised.
        try:
            log.debug(f"Cleaning up downloads for {ROUTINE}...")
            GarbageCollector().delete(cwd / "output" / "downloads" / ROUTINE)
        except Exception as e_gc:
            log.log(
                level=logging.WARNING,
                domain=domain,
                message=f"{domain} ROUTINE {ROUTINE} | Failed to clean downloads: {e_gc}",
            )

        # A SharePoint hiccup here shouldn't mask the routine's real result.
        # upload_log_file() logs its own success/failure line.
        try:
            log.debug(f"Sending final log to SharePoint for {ROUTINE}...")
            await asyncio.get_event_loop().run_in_executor(
                None, lambda: upload_log_file(domain=domain, routine=ROUTINE)
            )
        except Exception as e_log:
            log.debug(f"{domain} ROUTINE {ROUTINE} | Final log upload failed: {e_log}", level="ERRO")

        log.log(
            level=logging.INFO,
            domain=domain,
            message=f"{domain} ROUTINE {ROUTINE} | Main routine completed.",
        )
