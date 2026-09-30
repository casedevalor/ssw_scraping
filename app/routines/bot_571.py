import asyncio
import logging

from pathlib import Path
from typing import Tuple

from playwright.async_api import Playwright, Page, async_playwright

from config.settings.variables import settings
from app.core.login import perform_login
from app.core.upload_to_sharepoint import upload_report_file, upload_log_file
from app.utils.cleanup import GarbageCollector
from app.utils.datetime_utils import Dates
from app.utils.custom_logger import get_logger

cwd: Path = Path.cwd()
log = get_logger()
ROUTINE: str = "571"

# ---------------------------------------------------------------------------
# Selectors
# ---------------------------------------------------------------------------
SEL_DATE_START = '[id="\\32 8"]'
SEL_DATE_END = '[id="\\32 9"]'
SEL_ROUTINE_INPUT = '[id="\\33 "]'
SEL_ERROR_LABEL = "#errormsglabel"
SEL_BTN_PENDING = "Pendentes"
SEL_BTN_OK = "7. OK"

# ---------------------------------------------------------------------------
# Known business messages
# ---------------------------------------------------------------------------
MSG_NOT_RELEASED = "Opção não liberada"


def _build_date_range() -> Tuple[str, str]:
    """
    Fixed period for this routine: from the start of the year to today.

    NOTE: no day/hour gate, and not part of the "previous + current month"
    group (bot_002.py and siblings): year_to_date_range() already always
    covers the whole year so far, so there's no separate "backfill" case to
    add.
    """
    return Dates().year_to_date_range()


async def check_and_download_report(
    page: Page, attempt: int, max_attempts: int
) -> None:
    """
    Fills the report form, waits for a download or an error message,
    and uploads the file to SharePoint on success.

    Raises:
        TimeoutError: When neither a download nor a message appears within 60 s.
    """
    dates: Tuple[str, str] = _build_date_range()
    domain: str = settings.ssw_domain
    log.log(
        level=logging.INFO,
        domain=domain,
        message=f"{domain} ROUTINE {ROUTINE} | Start Dates: [{dates[0]} → {dates[1]}]",
    )

    await page.wait_for_timeout(1000)
    await page.locator(SEL_DATE_START).click()
    await page.locator(SEL_DATE_START).fill(dates[0])
    await page.wait_for_timeout(500)
    await page.locator(SEL_DATE_END).click()
    await page.locator(SEL_DATE_END).fill(dates[1])
    await page.wait_for_timeout(500)

    task_download = asyncio.create_task(page.wait_for_event("download"))
    task_msg = asyncio.create_task(
        page.locator(SEL_ERROR_LABEL).wait_for(state="visible")
    )

    await page.get_by_role("link", name=SEL_BTN_PENDING).click()

    done, pending = await asyncio.wait(
        [task_download, task_msg],
        timeout=60.0,
        return_when=asyncio.FIRST_COMPLETED,
    )

    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)

    # ------------------------------------------------------------------
    # SCENARIO 1 — Timeout
    # ------------------------------------------------------------------
    if not done:
        raise TimeoutError(
            f"{domain} ROUTINE {ROUTINE} | "
            "Timeout: no download and no message appeared."
        )

    # ------------------------------------------------------------------
    # SCENARIO 2 — Direct download
    # ------------------------------------------------------------------
    if task_download in done and not task_download.exception():
        download = task_download.result()
        file_name = f"20{dates[0][-2:]}-{domain}-{ROUTINE}.sswweb"

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
        return

    # ------------------------------------------------------------------
    # SCENARIO 3 — Message appeared
    # ------------------------------------------------------------------
    if task_msg in done and not task_msg.exception():
        text = await page.locator(SEL_ERROR_LABEL).inner_text()
        await page.get_by_role("link", name=SEL_BTN_OK).click()
        if text:
            log.log(
                level=logging.WARNING,
                domain=domain,
                message=f"{domain} ROUTINE {ROUTINE} | [{dates[0]} → {dates[1]}] {text}",
            )


async def run(playwright: Playwright, attempt: int, max_attempts: int) -> None:
    """
    Launches the browser, logs in, navigates to routine 571, races the
    popup against the menu-level error label (MSG_NOT_RELEASED), and
    delegates the download work to check_and_download_report().
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
            await check_and_download_report(page1, attempt, max_attempts)

    except Exception as e:
        log.log(
            level=logging.ERROR,
            domain=settings.ssw_domain,
            message=f"{settings.ssw_domain} ROUTINE {ROUTINE} | {e} - attempt {attempt}/{max_attempts}.",
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
