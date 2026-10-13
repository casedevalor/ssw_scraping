import asyncio
import logging

from datetime import datetime
from pathlib import Path
from typing import List, Tuple

from playwright.async_api import Playwright, Page, async_playwright

from config.settings.variables import settings
from app.core.downloads import process_156_report
from app.core.login import perform_login
from app.core.upload_to_sharepoint import upload_log_file
from app.utils.cleanup import GarbageCollector
from app.utils.datetime_utils import Dates
from app.utils.custom_logger import get_logger

cwd: Path = Path.cwd()
log = get_logger()
ROUTINE: str = "455ABH"

# ---------------------------------------------------------------------------
# Selectors
# ---------------------------------------------------------------------------
SEL_ROUTINE_INPUT = '[id="\\33 "]'
SEL_DATE_START = '[id="\\31 1"]'
SEL_DATE_END = '[id="\\31 2"]'
SEL_FIELD_E = '[id="\\33 5"]'
SEL_FIELD_A = '[id="\\33 7"]'
SEL_FIELD_B = '[id="\\33 8"]'
SEL_FIELD_H = '[id="\\33 9"]'
SEL_BTN_SUBMIT = "►"
SEL_BTN_OPEN_156 = "1. Abrir a opção 156 agora"
SEL_ERROR_LABEL = "#errormsglabel"
SEL_BTN_OK = "7. OK"

# ---------------------------------------------------------------------------
# Known business messages
# ---------------------------------------------------------------------------
MSG_NOT_RELEASED = "Opção não liberada"

# ---------------------------------------------------------------------------
# Weekend 12-month backfill gate (see _build_date_ranges)
# ---------------------------------------------------------------------------
BACKFILL_WEEKDAY: int = 5  # datetime.weekday(): Monday=0 ... Sunday=6 (Saturday)
BACKFILL_HOUR: int = 15


def _build_date_ranges() -> List[Tuple[str, str]]:
    """
    Picks the date ranges for this run:

    - Saturday at BACKFILL_HOUR: the last 12 full calendar months (current
      month excluded) — the weekly backfill run fired by wf_weekend.py.
    - Any other day/hour: the previous month (full) and the current month
      (to date) — two ranges, as in every other run.

    NOTE: this routine has its own day + hour gate, so it is not in the
    "always backfill last month" group (see bot_002.py). The gate was
    requested by the user on 2026-10-12 (455ABH on Saturday, 455CGK on
    Sunday, 455F on Saturday, all at 15h). The hour must match the cron
    time of WF_WE in this company's scheduler; change BACKFILL_HOUR here
    if that time differs.

    :return: 12 monthly ranges on the backfill run, otherwise
        [(previous_month_start, previous_month_end), (current_month_start, today)]
    """
    now: datetime = datetime.now()
    if now.weekday() == BACKFILL_WEEKDAY and now.hour == BACKFILL_HOUR:
        return Dates().previous_twelve_months_ranges()
    return Dates().previous_and_current_month_ranges()


async def _process_date_range(
    page: Page,
    domain: str,
    dates: Tuple[str, str],
    attempt: int,
    max_attempts: int,
) -> None:
    """
    Fills the 455 form for one date range (variant ABH), submits it and
    opens screen 156, where process_156_report() downloads the file, runs
    processing_csv_455ABH (known SSW renames), uploads it and then checks its columns.

    Raises:
        Exception: Propagated from the popup wait or process_156_report on failure.
    """
    # Unlike 455F (single field), ABH narrows the report through three
    # report-type fields, filled with A / B / H.
    await page.locator(SEL_DATE_START).fill(dates[0])
    await page.wait_for_timeout(200)
    await page.locator(SEL_DATE_END).fill(dates[1])
    await page.wait_for_timeout(200)
    await page.locator(SEL_FIELD_E).fill("E")
    await page.wait_for_timeout(300)
    await page.locator(SEL_FIELD_A).fill("A")
    await page.wait_for_timeout(200)
    await page.locator(SEL_FIELD_A).press("Tab")
    await page.locator(SEL_FIELD_B).fill("B")
    await page.wait_for_timeout(200)
    await page.locator(SEL_FIELD_B).press("Tab")
    await page.locator(SEL_FIELD_H).fill("H")
    await page.wait_for_timeout(200)

    await page.get_by_role("link", name=SEL_BTN_SUBMIT).click()

    # Routine 455ABH always routes through screen 156
    async with page.expect_popup() as page2_info:
        await page.get_by_role("link", name=SEL_BTN_OPEN_156).click()
    page2 = await page2_info.value
    await page2.wait_for_load_state("load")

    await process_156_report(page2, dates[0], ROUTINE, domain, attempt, max_attempts)


async def handler_download(
    page: Page, domain: str, attempt: int, max_attempts: int
) -> None:
    """
    Runs _process_date_range() once per range from _build_date_ranges()
    (previous month + current month to date, or the last 12 full months on
    the weekend backfill run).

    Each range is isolated — a failure in one does not stop the next — but
    if every range fails the error is re-raised so main()'s retry loop
    still engages.

    Raises:
        RuntimeError: When all date ranges failed.
    """
    date_ranges: List[Tuple[str, str]] = _build_date_ranges()
    failures: int = 0

    for dates in date_ranges:
        log.log(
            level=logging.INFO,
            domain=domain,
            message=f"{domain} ROUTINE {ROUTINE} | Start Dates: [{dates[0]} → {dates[1]}]",
        )
        try:
            await _process_date_range(page, domain, dates, attempt, max_attempts)
        except Exception as e_range:
            failures += 1
            log.log(
                level=logging.ERROR,
                domain=domain,
                message=f"{domain} ROUTINE {ROUTINE} | Failed to process [{dates[0]} → {dates[1]}]: {e_range}",
            )

    if failures == len(date_ranges):
        raise RuntimeError(
            f"{domain} ROUTINE {ROUTINE} | All {len(date_ranges)} date range(s) failed."
        )


async def run(playwright: Playwright, attempt: int, max_attempts: int) -> None:
    """
    Launches the browser, logs in, opens the shared 455 screen, races the
    popup against the menu-level error label (MSG_NOT_RELEASED), and
    delegates the download work to handler_download().
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

        # Navigate to routine 455 (shared screen for 455ABH/455CGK/455F)
        await page.locator(SEL_ROUTINE_INPUT).fill("455")
        await page.wait_for_timeout(250)

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
            if text:
                await page.get_by_role("link", name=SEL_BTN_OK).click()
                error_msg = f"ROUTINE {ROUTINE} | {text}"
                log.log(
                    level=logging.ERROR, domain=settings.ssw_domain, message=error_msg
                )
                raise RuntimeError(error_msg)

        if task_popup not in done or task_popup.exception():
            raise RuntimeError(f"ROUTINE {ROUTINE} | Popup did not open.")

        page1 = task_popup.result()
        await page1.wait_for_load_state("load")

        await handler_download(page1, settings.ssw_domain, attempt, max_attempts)

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
