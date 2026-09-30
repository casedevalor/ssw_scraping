import asyncio
import logging

from pathlib import Path
from typing import List

from playwright.async_api import Playwright, Page, async_playwright

from config.settings.variables import settings
from app.core.column_sentinel import check_file
from app.core.login import perform_login
from app.core.upload_to_sharepoint import upload_report_file, upload_log_file
from app.utils.cleanup import GarbageCollector
from app.utils.custom_logger import get_logger

cwd: Path = Path.cwd()
log = get_logger()
ROUTINE: str = "435"

# ---------------------------------------------------------------------------
# Selectors
# ---------------------------------------------------------------------------
SEL_FIELD_LIST = "#t_rel_lista"
SEL_FIELD_EXCEL = "#t_excel"
SEL_FIELD_MORTO = "#t_ler_morto"
SEL_ROUTINE_INPUT = '[id="\\33 "]'
SEL_ERROR_LABEL = "#errormsglabel"
SEL_BTN_SUBMIT = "►"
SEL_BTN_OK = "7. OK"

# ---------------------------------------------------------------------------
# Known business messages
# ---------------------------------------------------------------------------
MSG_NOT_RELEASED = "Opção não liberada"

# Report options: "S" → CSV, "N" → TXT
OPTION_CSV: str = "S"
OPTION_TXT: str = "N"
LIST_OPTIONS: List[str] = [OPTION_CSV, OPTION_TXT]


async def _process_option(
    page: Page, domain: str, option: str, attempt: int, max_attempts: int
) -> None:
    """
    Fills the form for one report option and races download vs. message:
    a download is saved (the CSV option is checked by the column sentinel)
    and uploaded; a message is dismissed and logged as WARNING — this screen
    has no catalogued messages yet, so none is treated as a failure.

    Raises:
        TimeoutError: When neither a download nor a message appears within 60 s.
    """
    await page.locator(SEL_FIELD_LIST).fill("T")
    await page.wait_for_timeout(250)
    await page.locator(SEL_FIELD_EXCEL).fill(option)
    await page.wait_for_timeout(250)
    await page.locator(SEL_FIELD_MORTO).fill("S")
    await page.wait_for_timeout(250)

    # Listen for both outcomes BEFORE submitting so neither can be missed.
    task_download = asyncio.create_task(page.wait_for_event("download"))
    task_msg = asyncio.create_task(
        page.locator(SEL_ERROR_LABEL).wait_for(state="visible")
    )

    await page.get_by_role("link", name=SEL_BTN_SUBMIT).click()

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
        extension: str = "csv" if option == OPTION_CSV else "txt"
        file_name = f"{domain}-{ROUTINE}.{extension}"
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
        # Only the CSV has a column header; the TXT is a text report.
        # After the upload, so its line closes this file's block in the log;
        # logs one line only and never raises.
        if option == OPTION_CSV:
            check_file(path_download / file_name, ROUTINE, domain)
        return

    # ------------------------------------------------------------------
    # SCENARIO 3 — Message appeared
    # ------------------------------------------------------------------
    if task_msg in done and not task_msg.exception():
        text = await page.locator(SEL_ERROR_LABEL).inner_text()
        log.log(
            level=logging.WARNING,
            domain=domain,
            message=f"{domain} ROUTINE {ROUTINE} | Option '{option}' | {text}",
        )
        await page.get_by_role("link", name=SEL_BTN_OK).click()


async def handler_download(
    page: Page, domain: str, attempt: int, max_attempts: int
) -> None:
    """
    Runs _process_option() once per entry in LIST_OPTIONS (CSV, then TXT).

    Each option is isolated — a failure in one does not stop the next — but
    if every option fails the error is re-raised so main()'s retry loop
    still engages.

    Raises:
        RuntimeError: When all options failed.
    """
    failures: int = 0

    for option in LIST_OPTIONS:
        try:
            await _process_option(page, domain, option, attempt, max_attempts)
        except Exception as e_option:
            failures += 1
            log.log(
                level=logging.ERROR,
                domain=domain,
                message=f"{domain} ROUTINE {ROUTINE} | Failed to process option '{option}': {e_option}",
            )

    if failures == len(LIST_OPTIONS):
        raise RuntimeError(
            f"{domain} ROUTINE {ROUTINE} | All {len(LIST_OPTIONS)} option(s) failed."
        )


async def run(playwright: Playwright, attempt: int, max_attempts: int) -> None:
    """
    Launches the browser, logs in, navigates to routine 435, races the
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
