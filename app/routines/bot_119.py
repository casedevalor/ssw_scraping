import asyncio
import logging

from typing import List, Tuple
from pathlib import Path

from playwright.async_api import Playwright, Page, async_playwright

from config.settings.variables import settings
from app.core.upload_to_sharepoint import upload_report_file, upload_log_file
from app.core.login import perform_login
from app.core.column_sentinel import check_file
from app.core.data_processing import Dataframe_Analytics
from app.utils.cleanup import GarbageCollector
from app.utils.datetime_utils import Dates
from app.utils.custom_logger import get_logger

# Current directory and routine name
cwd: Path = Path.cwd()
log = get_logger()
ROUTINE: str = "119"

# ---------------------------------------------------------------------------
# Selectors — centralized so any future ID change is a one-line fix
# ---------------------------------------------------------------------------
SEL_DATE_START = "#per_visita_ini_rel"
SEL_DATE_END = "#per_visita_fin_rel"
SEL_VEND_LOGIN = "#login_vend_rel"
SEL_BTN_SUBMIT = "#btn_env_rel"
SEL_ROUTINE_INPUT = '[id="\\33 "]'
SEL_ERROR_LABEL = "#errormsglabel"
SEL_BTN_OK = "7. OK"

# ---------------------------------------------------------------------------
# Known business messages — centralized and actually used
# ---------------------------------------------------------------------------
MSG_NO_RECORDS = "Nenhuma ocorrência encontrada"
MSG_NOT_RELEASED = "Opção não liberada"


def _build_date_ranges() -> List[Tuple[str, str]]:
    """
    Always returns the previous month (full) and the current month (to
    date) — two ranges, every run.

    NOTE: no day/hour gate. This routine belongs to the "always backfill
    last month" group whose helper is a one-to-one wrap of
    Dates().previous_and_current_month_ranges(): bot_002, bot_036, bot_073,
    bot_076, bot_119, bot_200, bot_324, bot_411, bot_442, bot_452, bot_467
    and bot_493. Don't add a gate here without an explicit business request
    (bot_455ABH/455CGK/455F left the group for their weekend backfill gate).

    :return: [(previous_month_start, previous_month_end), (current_month_start, today)]
    """
    return Dates().previous_and_current_month_ranges()


async def _process_date_range(
    page: Page,
    domain: str,
    dates: Tuple[str, str],
    attempt: int,
    max_attempts: int,
) -> None:
    """
    Fills the form for one date range and races download vs. message: a
    download (.sswweb) is converted to .xlsx by processing_sswweb_119,
    uploaded and then checked by the column sentinel; "no records" is logged as WARNING and returns normally.

    Raises:
        TimeoutError: When neither a download nor a message appears within 60 s.
        RuntimeError: On an unexpected SSW message.
    """
    # Fill out the report form fields
    await page.locator(SEL_DATE_START).click()
    await page.locator(SEL_DATE_START).fill(dates[0])
    await page.wait_for_timeout(250)
    await page.locator(SEL_DATE_END).click()
    await page.locator(SEL_DATE_END).fill(dates[1])
    await page.wait_for_timeout(250)
    await page.locator(SEL_VEND_LOGIN).click()
    await page.locator(SEL_VEND_LOGIN).fill("")
    await page.wait_for_timeout(250)

    # Listen for both outcomes BEFORE submitting so neither can be missed.
    task_download = asyncio.create_task(page.wait_for_event("download"))
    task_msg = asyncio.create_task(
        page.locator(SEL_ERROR_LABEL).wait_for(state="visible")
    )

    # Trigger the download
    await page.locator(SEL_BTN_SUBMIT).click()

    # Wait for whichever event happens first — SSW can be slow to generate the file.
    done, pending = await asyncio.wait(
        [task_download, task_msg],
        timeout=60.0,
        return_when=asyncio.FIRST_COMPLETED,
    )

    # Always cancel the task that lost the race to avoid memory leaks
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)

    # ------------------------------------------------------------------
    # SCENARIO 1 — Global timeout (system hung, no download, no warning)
    # ------------------------------------------------------------------
    if not done:
        raise TimeoutError("Timeout: no download started and no warning appeared.")

    # ------------------------------------------------------------------
    # SCENARIO 2 — Download started successfully
    # ------------------------------------------------------------------
    if task_download in done and not task_download.exception():
        download = task_download.result()

        file_name = (
            f"20{dates[0][-2:]}-{dates[0][2:4]}-{domain}-{ROUTINE}.sswweb"
        )
        path_download = cwd / "output" / "downloads" / ROUTINE
        path_download.mkdir(parents=True, exist_ok=True)
        await download.save_as(path_download / file_name)

        # Non-blocking data processing (Pandas conversion .sswweb -> .xlsx)
        loop = asyncio.get_event_loop()
        new_file_path = await loop.run_in_executor(
            None,
            lambda: Dataframe_Analytics().processing_sswweb_119(
                file_name=str(path_download / file_name)
            ),
        )
        new_file_name = Path(new_file_path).name

        await upload_report_file(
            file_name=new_file_name,
            routine=ROUTINE,
            domain=domain,
            attempt=attempt,
            max_attempts=max_attempts,
        )
        # After processing: the baseline describes the .xlsx (dormant today —
        # see config/sentinel/119.yml). Runs after the upload so its line
        # closes this file's block in the log; never raises.
        check_file(path_download / new_file_name, ROUTINE, domain)
        return

    # ------------------------------------------------------------------
    # SCENARIO 3 — Warning / error message appeared
    # ------------------------------------------------------------------
    if task_msg in done and not task_msg.exception():
        text: str = await page.locator(SEL_ERROR_LABEL).inner_text()

        if MSG_NO_RECORDS in text:
            await page.get_by_role("link", name=SEL_BTN_OK).click()
            log.log(
                level=logging.WARNING,
                domain=domain,
                message=(
                    f"{domain} ROUTINE {ROUTINE} | "
                    f"[{dates[0]} → {dates[1]}] {text}"
                ),
            )
            await asyncio.sleep(0.5)
            return

        # Unexpected message — dismiss the modal and raise; handler_download()
        # logs it and moves on to the next range.
        await page.get_by_role("link", name=SEL_BTN_OK).click()
        raise RuntimeError(f"Unexpected error message: {text}")


async def handler_download(
    page: Page, domain: str, attempt: int, max_attempts: int
) -> None:
    """
    Runs _process_date_range() once per range from _build_date_ranges()
    (previous month + current month to date).

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
    Launches the browser, logs in, navigates to routine 119, races the
    popup against the menu-level error label (MSG_NOT_RELEASED), and
    delegates the download work to handler_download().

    :param playwright: Playwright instance to manage the browser.
    :param attempt: Current attempt number.
    :param max_attempts: Maximum number of attempts.
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

        # Perform login
        await perform_login(page, ROUTINE)

        # Access the routine page and prepare for download
        await page.locator(SEL_ROUTINE_INPUT).click()
        await page.locator(SEL_ROUTINE_INPUT).fill(ROUTINE)
        await page.wait_for_timeout(1000)

        # Race popup vs. menu-level error label instead of assuming the popup
        # always opens — a routine that is "Opção não liberada" for this
        # domain never opens a popup, only the error label.
        task_popup = asyncio.create_task(page.wait_for_event("popup"))
        task_error = asyncio.create_task(
            page.locator(SEL_ERROR_LABEL).wait_for(state="visible")
        )

        await page.locator(SEL_ROUTINE_INPUT).press("Tab")

        done, pending = await asyncio.wait(
            [task_popup, task_error], timeout=10.0, return_when=asyncio.FIRST_COMPLETED
        )

        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)

        # SCENARIO 1 — Timeout
        if not done:
            raise TimeoutError(
                f"ROUTINE {ROUTINE} | Timeout: neither popup nor error "
                "appeared after pressing Tab."
            )

        # SCENARIO 2 — Error message on screen
        if task_error in done and not task_error.exception():
            text = await page.locator(SEL_ERROR_LABEL).inner_text()
            if text:
                await page.get_by_role("link", name=SEL_BTN_OK).click()
                error_msg = f"ROUTINE {ROUTINE} |  {text}"
                log.log(
                    level=logging.ERROR, domain=settings.ssw_domain, message=error_msg
                )
                raise RuntimeError(error_msg)

        # SCENARIO 3 — Popup opened successfully
        if task_popup in done and not task_popup.exception():
            page1 = task_popup.result()
            await page1.wait_for_load_state("load")

            await handler_download(page1, settings.ssw_domain, attempt, max_attempts)

    except Exception as e:
        log.log(
            level=logging.ERROR,
            domain=settings.ssw_domain,
            message=f"{settings.ssw_domain} ROUTINE {ROUTINE} | {str(e)} - attempt {attempt}/{max_attempts}.",
        )
        raise  # plain `raise` preserves the original traceback
    finally:
        if context:
            await context.close()
        if browser:
            await browser.close()


async def main() -> None:
    """
    Initializes Playwright, runs the main routine with retry logic, and cleans up downloaded files after completion.
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
                            message=f"{settings.ssw_domain} ROUTINE {ROUTINE} | {error_msg}. Aborting attempts.",
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
