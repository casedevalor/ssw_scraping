import asyncio
import logging

from pathlib import Path
from typing import List, Tuple

from playwright.async_api import Playwright, Page, async_playwright

from config.settings.variables import settings
from app.core.column_sentinel import check_file
from app.core.login import perform_login
from app.core.upload_to_sharepoint import upload_report_file, upload_log_file
from app.utils.cleanup import GarbageCollector
from app.utils.datetime_utils import Dates
from app.utils.custom_logger import get_logger

cwd: Path = Path.cwd()
log = get_logger()
ROUTINE: str = "002"

# ---------------------------------------------------------------------------
# Selectors
# ---------------------------------------------------------------------------
SEL_TYPE_FILTER = '[id="\\36 "]'
SEL_DATE_START = '[id="\\31 7"]'
SEL_DATE_END = '[id="\\31 8"]'
SEL_BTN_SUBMIT = '[id="\\31 9"]'
SEL_ROUTINE_INPUT = '[id="\\33 "]'
SEL_ERROR_LABEL = "#errormsglabel"
SEL_OK_BUTTON = "7. OK"

# ---------------------------------------------------------------------------
# Known business messages
# ---------------------------------------------------------------------------
MSG_NO_RECORDS = "Não existem cotações cadastradas para data informada"
MSG_SUCCESS = "Relatório gerado com sucesso."
MSG_NOT_RELEASED = "Opção não liberada"


def _build_date_ranges() -> List[Tuple[str, str]]:
    """
    Always returns the previous month (full) and the current month (to
    date) — two ranges, every run.

    NOTE: no day/hour gate. This routine belongs to the "always backfill
    last month" group whose helper is a one-to-one wrap of
    Dates().previous_and_current_month_ranges(): bot_002, bot_036, bot_073,
    bot_076, bot_119, bot_200, bot_324, bot_411, bot_442, bot_452,
    bot_455ABH, bot_455CGK, bot_455F, bot_467 and bot_493. Don't add a gate
    here without an explicit business request.

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
    Fills the form for one date range, races download vs. modal, and on
    success saves the file, uploads it and then checks its columns — either
    directly or after the "report generated" prompt. "No records" is
    logged as WARNING and returns normally.

    Raises:
        TimeoutError: When neither a download nor a message appears within 60 s.
        RuntimeError: On an unexpected SSW message.
        Exception: A real Playwright download error is propagated as-is.
    """
    await page.wait_for_timeout(1000)

    # "E" is the type filter this screen requires before it will accept dates.
    await page.locator(SEL_TYPE_FILTER).fill("E")
    await page.wait_for_timeout(250)
    await page.locator(SEL_DATE_START).fill(dates[0])
    await page.wait_for_timeout(250)
    await page.locator(SEL_DATE_END).fill(dates[1])
    await page.wait_for_timeout(250)

    # Start listening for both possible outcomes BEFORE submitting, so we
    # can't miss whichever one fires first (direct download vs. a modal).
    task_download = asyncio.create_task(page.wait_for_event("download"))
    task_warning = asyncio.create_task(
        page.locator(SEL_ERROR_LABEL).wait_for(state="visible")
    )
    await page.wait_for_timeout(1000)

    await page.locator(SEL_BTN_SUBMIT).click()
    await page.wait_for_timeout(500)

    # Race the two outcomes instead of waiting a fixed amount of time —
    # react to whichever happens first.
    done, pending = await asyncio.wait(
        [task_download, task_warning],
        timeout=60.0,
        return_when=asyncio.FIRST_COMPLETED,
    )

    # Cancel and await the loser so its task exception doesn't get logged
    # as "never retrieved" by asyncio.
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)

    # ------------------------------------------------------------------
    # SCENARIO 1 — Timeout: SSW never responded either way
    # ------------------------------------------------------------------
    if not done:
        raise TimeoutError("Timeout: no download and no warning appeared.")

    # ------------------------------------------------------------------
    # SCENARIO 2 — Direct download: report was ready immediately
    # ------------------------------------------------------------------
    if task_download in done:
        if task_download.exception():
            # Propagate the real Playwright error instead of masking it.
            raise task_download.exception()

        await page.wait_for_timeout(250)
        download = task_download.result()
        await page.wait_for_timeout(250)
        # SSW shows a confirmation modal after the download starts — dismiss it.
        await page.get_by_role("link", name=SEL_OK_BUTTON).click()

        file_name = f"20{dates[0][-2:]}-{dates[0][2:4]}-{domain}-{ROUTINE}.csv"
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
        return

    # ------------------------------------------------------------------
    # SCENARIO 3 — A modal appeared instead of a download; read it to
    # decide what it actually means.
    # ------------------------------------------------------------------
    if task_warning in done and not task_warning.exception():
        text: str = await page.locator(SEL_ERROR_LABEL).inner_text()
        await page.wait_for_timeout(250)

        # 3a — No data for this period: not an error, just nothing to fetch.
        if MSG_NO_RECORDS in text:
            await page.get_by_role("link", name=SEL_OK_BUTTON).click()
            log.log(
                level=logging.WARNING,
                domain=domain,
                message=f"{domain} ROUTINE {ROUTINE} | [{dates[0]} → {dates[1]}] {text}",
            )
            return

        # 3b — Report was generated asynchronously: dismiss the notice and
        # click submit again — this time it triggers the actual download.
        if MSG_SUCCESS in text:
            await page.wait_for_timeout(500)
            await page.get_by_role("link", name=SEL_OK_BUTTON).click()
            await page.wait_for_timeout(250)

            async with page.expect_download(timeout=120_000) as download_info:
                await page.locator(SEL_BTN_SUBMIT).click()
            download = await download_info.value
            await page.wait_for_timeout(500)

            # A second confirmation modal may or may not show up here.
            ok_btn = page.get_by_role("link", name=SEL_OK_BUTTON)
            if await ok_btn.is_visible():
                await ok_btn.click()

            file_name = (
                f"20{dates[0][-2:]}-{dates[0][2:4]}-{domain}-{ROUTINE}.csv"
            )
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
            return

        # 3c — Anything else is a genuine, unexpected failure — surface it.
        await page.get_by_role("link", name=SEL_OK_BUTTON).click()
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
    Launches the browser, logs in, navigates to routine 002, races the
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

        # Menu-level error (MSG_NOT_RELEASED for this domain) — dismiss it and
        # re-raise so main() decides whether to retry.
        if task_error in done and not task_error.exception():
            text = await page.locator(SEL_ERROR_LABEL).inner_text()
            if text:
                await page.get_by_role("link", name=SEL_OK_BUTTON).click()
                error_msg = f"ROUTINE {ROUTINE} | {text}"
                log.log(
                    level=logging.ERROR, domain=settings.ssw_domain, message=error_msg
                )
                raise RuntimeError(error_msg)

        # Popup opened successfully — hand off to the actual download logic.
        if task_popup in done and not task_popup.exception():
            page1 = task_popup.result()
            await page1.wait_for_load_state("load")
            await handler_download(page1, settings.ssw_domain, attempt, max_attempts)

    except Exception as e:
        log.log(
            level=logging.ERROR,
            domain=settings.ssw_domain,
            message=f"{settings.ssw_domain} ROUTINE {ROUTINE} | {e} - attempt {attempt}/{max_attempts}.",
        )
        raise
    finally:
        # Always release browser resources, even on failure.
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

                    # Non-retriable: the option genuinely isn't available —
                    # retrying wouldn't change SSW's answer.
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
