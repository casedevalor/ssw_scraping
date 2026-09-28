import asyncio
import logging

from pathlib import Path
from typing import List, Tuple

from playwright.async_api import Playwright, Page, async_playwright

from config.settings.variables import settings
from app.core.column_sentinel import check_file
from app.core.downloads import process_156_report
from app.core.login import perform_login
from app.core.upload_to_sharepoint import upload_report_file, upload_log_file
from app.utils.cleanup import GarbageCollector
from app.utils.datetime_utils import Dates
from app.utils.custom_logger import get_logger

cwd: Path = Path.cwd()
log = get_logger()
ROUTINE: str = "477"

# ---------------------------------------------------------------------------
# Selectors
# ---------------------------------------------------------------------------
SEL_DATE_START = "#data_ini_inclusao_despesa"
SEL_DATE_END = "#data_fin_inclusao_despesa"
SEL_ROUTINE_INPUT = '[id="\\33 "]'
SEL_ERROR_LABEL = "#errormsglabel"
SEL_BTN_EXCEL = "Arquivo Excel"
SEL_BTN_OK = "7. OK"
SEL_BTN_OPEN_156 = "1. Abrir a opção 156 agora"
SEL_ERROR_PANEL = "#errorpanel"

# ---------------------------------------------------------------------------
# Known business messages
# ---------------------------------------------------------------------------
MSG_GOTO_156 = (
    "Solicitação enviada para processamento. Acompanhe sua execução pela opção 156."
)
MSG_SUCCESS = "Relatório gerado com sucesso."
MSG_NO_RELEASES = (
    "Não há lançamento(s) para os parâmetros informados ou pertence a outra unidade."
)
MSG_NOT_RELEASED = "Opção não liberada"


def _build_date_ranges() -> List[Tuple[str, str]]:
    """
    Fixed periods for this routine: the last three years up to today.

    NOTE: no day/hour gate, and not part of the "previous + current month"
    group (bot_002.py and siblings): last_three_years_to_date_ranges()
    already always covers the full 3-year window, so there's no separate
    "backfill" case to add. Same shape as bot_441.py.
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
    shows after "Arquivo Excel":
        - Queued for 156  → process_156_report() downloads, checks columns, uploads
        - Report generated → dismiss, click "Arquivo Excel" again to download,
                             check columns, upload
        - No records      → WARNING, returns normally

    Raises:
        TimeoutError: When no message appears within 60 s.
        RuntimeError: On an unexpected SSW message.
        Exception: When the secondary download or process_156_report fails.
    """
    start_date, end_date = dates
    await page.wait_for_timeout(1000)
    await page.locator(SEL_DATE_START).fill(start_date)
    await page.wait_for_timeout(200)
    await page.locator(SEL_DATE_END).fill(end_date)
    await page.wait_for_timeout(200)

    # This screen never downloads on the first click: the outcome is always a
    # message, so only the error label is awaited.
    task_msg = asyncio.create_task(
        page.locator(SEL_ERROR_LABEL).wait_for(state="visible")
    )

    await page.get_by_role("link", name=SEL_BTN_EXCEL).click()

    done, pending = await asyncio.wait(
        [task_msg],
        timeout=60.0,
        return_when=asyncio.FIRST_COMPLETED,
    )

    # Cancel and await to suppress "Task exception was never retrieved"
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
    # SCENARIO 2 — Queued for screen 156
    # ------------------------------------------------------------------
    if MSG_GOTO_156 in text:
        async with page.expect_popup() as page2_info:
            await page.get_by_role("link", name=SEL_BTN_OPEN_156).click()
        page2 = await page2_info.value
        await page2.wait_for_load_state("load")
        await process_156_report(page2, start_date, ROUTINE, domain, attempt, max_attempts)
        log.log(
            level=logging.INFO,
            domain=domain,
            message=(
                f"{domain} ROUTINE {ROUTINE} | "
                f"[{start_date} → {end_date}] Processed via screen 156."
            ),
        )
        return

    # ------------------------------------------------------------------
    # SCENARIO 3 — Report generated locally
    # ------------------------------------------------------------------
    if MSG_SUCCESS in text:
        await page.get_by_role("link", name=SEL_BTN_OK).click()

        # Wait for the errorpanel to fully close before clicking
        # "Arquivo Excel" again — the div intercepts pointer events
        # while the modal is still visible, causing a 120 s timeout
        # on the click.
        await page.locator(SEL_ERROR_PANEL).wait_for(state="hidden")
        await page.wait_for_timeout(300)

        async with page.expect_download(timeout=120_000) as download_info:
            await page.get_by_role("link", name=SEL_BTN_EXCEL).click()
        download = await download_info.value

        await page.wait_for_timeout(1000)
        await page.get_by_role("link", name=SEL_BTN_OK).click()

        file_name = f"20{start_date[-2:]}-{domain}-{ROUTINE}.csv"
        path_download = cwd / "output" / "downloads" / ROUTINE
        path_download.mkdir(parents=True, exist_ok=True)
        await download.save_as(path_download / file_name)
        # Logs one line only; a column drift never blocks the upload.
        check_file(path_download / file_name, ROUTINE, domain)

        await upload_report_file(
            file_name=file_name,
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
                f"[{start_date} → {end_date}] '{file_name}' downloaded and uploaded successfully."
            ),
        )
        return

    # ------------------------------------------------------------------
    # SCENARIO 4 — No records
    # ------------------------------------------------------------------
    if MSG_NO_RELEASES in text:
        await page.get_by_role("link", name=SEL_BTN_OK).click()
        log.log(
            level=logging.WARNING,
            domain=domain,
            message=f"{domain} ROUTINE {ROUTINE} | [{start_date} → {end_date}] {text}",
        )
        return

    # ------------------------------------------------------------------
    # SCENARIO 5 — Unexpected message: dismiss it and raise, so the loop
    # logs it as ERROR and counts the range as failed.
    # ------------------------------------------------------------------
    await page.get_by_role("link", name=SEL_BTN_OK).click()
    raise RuntimeError(f"Unexpected message: {text}")


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
    Launches the browser, logs in, navigates to routine 477, races the
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
            log.debug("Sending final log to SharePoint...")
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
