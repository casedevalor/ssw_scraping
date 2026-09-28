import asyncio
import logging

from pathlib import Path
from typing import List, Tuple

from playwright.async_api import Playwright, Page, async_playwright

from config.settings.variables import settings
from app.core.column_sentinel import check_file
from app.core.data_processing import Dataframe_Analytics
from app.core.login import perform_login
from app.core.downloads import process_156_report
from app.core.upload_to_sharepoint import upload_report_file, upload_log_file
from app.utils.cleanup import GarbageCollector
from app.utils.datetime_utils import Dates
from app.utils.custom_logger import get_logger

cwd: Path = Path.cwd()
log = get_logger()
ROUTINE: str = "441"

# ---------------------------------------------------------------------------
# Selectors
# ---------------------------------------------------------------------------
SEL_FIELD_TP_ARQUIVO = "#tp_arquivo"
SEL_FIELD_FG_DATA = "#rel_ana_fg_data"
SEL_FIELD_DATE_START = "#rel_ana_per_pesq_ini"
SEL_FIELD_DATE_END = "#rel_ana_per_pesq_fin"
SEL_FIELD_EXCEL = "#rel_ana_arq_excel"
SEL_BTN_SUBMIT = "#btn_env_rel_ana"
SEL_ERROR_LABEL = "#errormsglabel"
SEL_ROUTINE_INPUT = '[id="\\33 "]'
SEL_BTN_OK = "7. OK"
SEL_BTN_OPEN_156 = "1. Abrir a opção 156 agora"

# ---------------------------------------------------------------------------
# Known business messages
# ---------------------------------------------------------------------------
MSG_NO_INVOICES = "Não selecionou faturas para impressão"
MSG_156 = "156"
MSG_NOT_RELEASED = "Opção não liberada"


def _build_date_ranges() -> List[Tuple[str, str]]:
    """
    Determines the date ranges to process: the last three years up to today.

    NOTE: no day/hour gate, and not part of the "previous + current month"
    group (bot_002.py and siblings): routine 441 always needs the full
    3-year window, so this wraps Dates().last_three_years_to_date_ranges()
    one-to-one. Same shape as bot_477.py.
    """
    return Dates().last_three_years_to_date_ranges()


async def _process_date_range(
    page: Page,
    domain: str,
    dates: Tuple[str, str],
    attempt: int,
    max_attempts: int,
) -> None:
    """
    Fills the form for one date range and routes on the message SSW always
    shows after submitting:
        - No invoices  → WARNING, returns normally
        - 156 message  → process_156_report() downloads, processes and uploads
        - Other/success → dismiss, click submit again to trigger the download,
                          process (processing_csv_441), check columns and upload

    Raises:
        TimeoutError: When no message appears within 60 s.
        Exception: When the secondary download or process_156_report fails.
    """
    start_date, end_date = dates
    await page.locator(SEL_FIELD_TP_ARQUIVO).fill("F")
    await page.wait_for_timeout(250)
    await page.locator(SEL_FIELD_FG_DATA).fill("E")
    await page.wait_for_timeout(250)
    await page.locator(SEL_FIELD_DATE_START).fill(start_date)
    await page.wait_for_timeout(250)
    await page.locator(SEL_FIELD_DATE_END).fill(end_date)
    await page.wait_for_timeout(250)
    await page.locator(SEL_FIELD_EXCEL).fill("S")
    await page.wait_for_timeout(250)

    # asyncio.wait ensures the modal is visible before we read its text —
    # reading it right after the click would race the modal's render
    # and could return an empty string.
    task_msg = asyncio.create_task(
        page.locator(SEL_ERROR_LABEL).wait_for(state="visible")
    )

    await page.locator(SEL_BTN_SUBMIT).click()
    await page.wait_for_timeout(500)

    done, pending = await asyncio.wait(
        [task_msg],
        timeout=60.0,
        return_when=asyncio.FIRST_COMPLETED,
    )

    # Cancel and await the loser so its exception isn't reported as
    # "never retrieved" by asyncio.
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)

    # ------------------------------------------------------------------
    # SCENARIO 1 — Timeout
    # ------------------------------------------------------------------
    if not done:
        raise TimeoutError("Timeout waiting for response.")

    text = await page.locator(SEL_ERROR_LABEL).inner_text()

    # ------------------------------------------------------------------
    # SCENARIO 2 — No invoices selected
    # ------------------------------------------------------------------
    if MSG_NO_INVOICES in text:
        await page.get_by_role("link", name=SEL_BTN_OK).click()
        log.log(
            level=logging.WARNING,
            domain=domain,
            message=(
                f"{domain} ROUTINE {ROUTINE} | "
                f"[{start_date} → {end_date}] No invoices selected for printing."
            ),
        )
        return

    # ------------------------------------------------------------------
    # SCENARIO 3 — Report queued in screen 156
    # ------------------------------------------------------------------
    if MSG_156 in text:
        async with page.expect_popup() as page2_info:
            await page.get_by_role("link", name=SEL_BTN_OPEN_156).click()
        page2 = await page2_info.value
        await process_156_report(
            page2,
            start_date,
            ROUTINE,
            domain,
            attempt,
            max_attempts,
        )
        log.log(
            level=logging.INFO,
            domain=domain,
            message=(
                f"{domain} ROUTINE {ROUTINE} | "
                f"[{start_date} → {end_date}] Download and upload successful."
            ),
        )
        return

    # ------------------------------------------------------------------
    # SCENARIO 4 — Success: dismiss modal and trigger direct download
    # ------------------------------------------------------------------
    await page.get_by_role("link", name=SEL_BTN_OK).click()
    await page.wait_for_timeout(500)

    async with page.expect_download(timeout=120_000) as download_info:
        await page.locator(SEL_BTN_SUBMIT).click()
    download = await download_info.value
    await page.wait_for_timeout(1000)

    file_name = f"20{start_date[-2:]}-{domain}-{ROUTINE}.csv"
    path_download = cwd / "output" / "downloads" / ROUTINE
    path_download.mkdir(parents=True, exist_ok=True)
    full_file_path = path_download / file_name
    await download.save_as(full_file_path)

    # processing_csv_441 may rename/move the file during cleanup, so the
    # upload filename is derived from its returned path, not file_name.
    processed_path = Dataframe_Analytics().processing_csv_441(
        file_path=str(full_file_path)
    )
    final_file_name = Path(processed_path).name
    # After processing: the baseline describes the processed file (dormant
    # today — see config/sentinel/441.yml). Logs one line, never blocks.
    check_file(path_download / final_file_name, ROUTINE, domain)

    await upload_report_file(
        file_name=final_file_name,
        routine=ROUTINE,
        domain=domain,
        attempt=attempt,
        max_attempts=max_attempts,
    )
    log.log(
        level=logging.INFO,
        domain=domain,
        message=(
            f"{domain} ROUTINE {ROUTINE} | "
            f"[{start_date} → {end_date}] '{final_file_name}' downloaded and uploaded successfully."
        ),
    )


async def handler_download(
    page: Page, domain: str, attempt: int, max_attempts: int
) -> None:
    """
    Runs _process_date_range() once per range from _build_date_ranges()
    (the last three years up to today).

    Each range is isolated — a failure in one does not stop the next — but
    if every range fails the error is re-raised so main()'s retry loop
    still engages.

    Raises:
        RuntimeError: When all date ranges failed.
    """
    date_ranges: List[Tuple[str, str]] = _build_date_ranges()
    failures: int = 0

    for dates in date_ranges:
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
    Launches the browser, logs in, navigates to routine 441, races the
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
                            message=f"Attempt {attempt} failed. Retrying in 5s...",
                        )
                        await asyncio.sleep(5)
    finally:
        # Runs exactly once, regardless of how many attempts happened or
        # how the routine ended.
        log.log(
            level=logging.INFO,
            domain=settings.ssw_domain,
            message=f"INFO - {ROUTINE} - Main routine completed. Starting final cleanup...",
        )

        # A SharePoint hiccup here shouldn't mask the routine's real result.
        try:
            log.debug(f"Sending final log to SharePoint for {ROUTINE}...")
            await asyncio.get_event_loop().run_in_executor(
                None, lambda: upload_log_file(domain=settings.ssw_domain)
            )
        except Exception as e_log:
            log.log(
                level=logging.ERROR,
                domain=settings.ssw_domain,
                message=f"CRITICAL ERROR: Failed to send final log for {ROUTINE}: {e_log}",
            )

        # Same reasoning: a cleanup failure is logged, not raised.
        try:
            log.debug(f"Cleaning up downloads for {ROUTINE}...")
            GarbageCollector().delete(cwd / "output" / "downloads" / ROUTINE)
        except Exception as e_gc:
            log.log(
                level=logging.WARNING,
                domain=settings.ssw_domain,
                message=f"WARNING - {ROUTINE} - Failed to clean downloads: {e_gc}",
            )
