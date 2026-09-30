import asyncio
import logging

from typing import List, Tuple
from pathlib import Path

from playwright.async_api import Playwright, Page, async_playwright

from config.settings.variables import settings
from app.core.column_sentinel import check_file
from app.core.downloads import process_156_report
from app.core.login import perform_login
from app.core.upload_to_sharepoint import upload_report_file, upload_log_file
from app.utils.datetime_utils import Dates
from app.utils.cleanup import GarbageCollector
from app.utils.custom_logger import get_logger

cwd: Path = Path.cwd()
log = get_logger()
ROUTINE: str = "306"

# ---------------------------------------------------------------------------
# Selectors
# ---------------------------------------------------------------------------
SEL_DATE_START = "#t_data_busca"
SEL_DATE_END = "#t_data_busca2"
SEL_ERROR_LABEL = "#errormsglabel"
SEL_ROUTINE_INPUT = '[id="\\33 "]'
SEL_BTN_EXCEL = "Excel"
SEL_BTN_OK = "7. OK"
SEL_BTN_OPEN_156 = "1. Abrir a opção 156 agora"

# ---------------------------------------------------------------------------
# Known business messages
# ---------------------------------------------------------------------------
MSG_SUCCESS = "Relatório gerado com sucesso"
MSG_156 = "156"
MSG_PROCESSING = "processamento"
MSG_NOT_RELEASED = "Opção não liberada"


def _build_date_ranges() -> List[Tuple[str, str]]:
    """
    Fixed periods for this routine: the previous fortnight (complete) and the
    current fortnight up to today — always 2 ranges.

    NOTE: no day/hour gate, and not part of the "previous + current month"
    group (bot_002.py and siblings): fortnight windows are what the business
    rule asks for here, and previous_and_current_fortnight_ranges() already
    always returns both, so there's no separate "backfill" case to add.
    """
    return Dates().previous_and_current_fortnight_ranges()


async def _process_date_range(
    page: Page,
    domain: str,
    dates: Tuple[str, str],
    attempt: int,
    max_attempts: int,
) -> None:
    """
    Fills the form for one fortnight and races download vs. message: a
    direct download (or the one triggered after the "report generated"
    prompt) is saved, uploaded and then checked by the column sentinel; a
    screen-156 notice is handed to process_156_report(), which does the
    same; any other message is dismissed and logged as WARNING.

    Raises:
        TimeoutError: When neither a download nor a message appears within 60 s.
        Exception: When the secondary download or process_156_report fails.
    """
    initial_date, final_date = dates
    await page.locator(SEL_DATE_START).focus()
    await page.locator(SEL_DATE_START).fill(initial_date)
    await page.wait_for_timeout(250)
    await page.locator(SEL_DATE_START).press("Tab")
    await page.locator(SEL_DATE_END).fill(final_date)
    await page.wait_for_timeout(250)

    # Listen for both outcomes BEFORE clicking so neither can be missed:
    # a direct download or a modal (prompt / screen 156 / other message).
    task_download = asyncio.create_task(page.wait_for_event("download"))
    task_msg = asyncio.create_task(
        page.locator(SEL_ERROR_LABEL).wait_for(state="visible")
    )

    await page.get_by_role("link", name=SEL_BTN_EXCEL).click()

    done, pending = await asyncio.wait(
        [task_download, task_msg],
        timeout=60.0,
        return_when=asyncio.FIRST_COMPLETED,
    )

    # Cancel the losing task AND await it to suppress
    # "Task exception was never retrieved" from Playwright's internal timeout
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)

    # ------------------------------------------------------------------
    # SCENARIO 1 — Timeout
    # ------------------------------------------------------------------
    if not done:
        raise TimeoutError("Timeout waiting for report generation.")

    # ------------------------------------------------------------------
    # SCENARIO 2 — Direct download
    # ------------------------------------------------------------------
    if task_download in done and not task_download.exception():
        download = task_download.result()

        # A confirmation modal may or may not follow the download.
        ok_btn = page.get_by_role("link", name=SEL_BTN_OK)
        if await ok_btn.is_visible():
            await ok_btn.click()

        file_suffix = "-1.csv" if initial_date[:2] == "01" else "-2.csv"
        file_name = f"20{initial_date[-2:]}-{initial_date[2:4]}-{domain}-{ROUTINE}{file_suffix}"

        path_download = cwd / "output" / "downloads" / ROUTINE
        path_download.mkdir(parents=True, exist_ok=True)
        await download.save_as(path_download / file_name)

        await upload_report_file(
            file_name=file_name,
            routine=ROUTINE,
            domain=domain,
            attempt=attempt,
            max_attempts=max_attempts,
        )
        # After the upload, so its line closes this file's block in the log;
        # logs one line only and never raises.
        check_file(path_download / file_name, ROUTINE, domain)
        return

    # ------------------------------------------------------------------
    # SCENARIO 3 — Message appeared
    # ------------------------------------------------------------------
    if task_msg in done and not task_msg.exception():
        text = await page.locator(SEL_ERROR_LABEL).inner_text()
        await page.wait_for_timeout(
            500
        )  # Allow any UI changes to settle before interacting
        if MSG_SUCCESS in text:
            # Report generated — dismiss the notice and click Excel again;
            # this time it triggers the actual download.
            await page.get_by_role("link", name=SEL_BTN_OK).click()
            await page.wait_for_timeout(500)

            async with page.expect_download(timeout=120_000) as download_info:
                await page.get_by_role("link", name=SEL_BTN_EXCEL).click()
            download = await download_info.value
            await page.wait_for_timeout(1000)

            ok_btn = page.get_by_role("link", name=SEL_BTN_OK)
            if await ok_btn.is_visible():
                await ok_btn.click()

            file_suffix = "-1.csv" if initial_date[:2] == "01" else "-2.csv"
            file_name = f"20{initial_date[-2:]}-{initial_date[2:4]}-{domain}-{ROUTINE}{file_suffix}"

            path_download = cwd / "output" / "downloads" / ROUTINE
            path_download.mkdir(parents=True, exist_ok=True)
            await download.save_as(path_download / file_name)

            await upload_report_file(
                file_name=file_name,
                routine=ROUTINE,
                domain=domain,
                attempt=attempt,
                max_attempts=max_attempts,
            )
            check_file(path_download / file_name, ROUTINE, domain)

        elif MSG_156 in text or MSG_PROCESSING in text:
            # Report sent to screen 156 — process_156_report() downloads,
            # uploads it and checks its columns.
            async with page.expect_popup() as page2_info:
                await page.get_by_role("link", name=SEL_BTN_OPEN_156).click()
            page2 = await page2_info.value
            await process_156_report(
                page2, initial_date, ROUTINE, domain, attempt, max_attempts
            )

        else:
            # Unexpected message — dismiss and log
            await page.get_by_role("link", name=SEL_BTN_OK).click()
            log.log(
                level=logging.WARNING,
                domain=domain,
                message=f"{domain} ROUTINE {ROUTINE} | [{initial_date} → {final_date}] {text}",
            )


async def handler_download(
    page: Page, domain: str, attempt: int, max_attempts: int
) -> None:
    """
    Runs _process_date_range() once per range from _build_date_ranges()
    (previous fortnight + current fortnight to date).

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
    Launches the browser, logs in, navigates to routine 306, races the
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
            if text:
                await page.get_by_role("link", name=SEL_BTN_OK).click()
                error_msg = f"ROUTINE {ROUTINE} | {text}"
                log.log(
                    level=logging.ERROR, domain=settings.ssw_domain, message=error_msg
                )
                raise RuntimeError(error_msg)

        if task_popup in done and not task_popup.exception():
            page1 = task_popup.result()
            await page1.wait_for_load_state("load")
            await handler_download(page1, settings.ssw_domain, attempt, max_attempts)

    except Exception as e:
        log.log(
            level=logging.ERROR,
            domain=settings.ssw_domain,
            message=(
                f"{settings.ssw_domain} ROUTINE {ROUTINE} | "
                f"{str(e)} - attempt {attempt}/{max_attempts}."
            ),
        )
        raise
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

                    # Non-retriable: routine not released for this user
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
