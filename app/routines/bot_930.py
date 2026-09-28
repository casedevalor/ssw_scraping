import asyncio
import logging

from pathlib import Path
from typing import List, Optional

from playwright.async_api import Playwright, Page, async_playwright

from config.settings.variables import settings
from app.core.downloads import process_156_report
from app.core.login import perform_login
from app.core.upload_to_sharepoint import upload_log_file

# from app.integrations.recaptcha import Recaptcha
from app.utils.cleanup import GarbageCollector
from app.utils.datetime_utils import Dates
from app.utils.custom_logger import get_logger

cwd: Path = Path.cwd()
log = get_logger()
ROUTINE: str = "930"

# ---------------------------------------------------------------------------
# Selectors
# ---------------------------------------------------------------------------
SEL_ROUTINE_INPUT = '[id="\\33 "]'
SEL_DATE_START = '[id="\\31 "]'
SEL_DATE_END = '[id="\\32 "]'
SEL_ERROR_LABEL = "#errormsglabel"
SEL_BTN_SUBMIT = "►"
SEL_BTN_OPEN_156 = "1. Abrir a opção 156 agora"
SEL_BTN_OK = "7. OK"
SEL_CAPTCHA = "#captcha"

# ---------------------------------------------------------------------------
# Known business messages
# ---------------------------------------------------------------------------
MSG_156 = "156"
MSG_PROCESSING = "processamento"
MSG_NO_RECORDS = "Nenhum registro encontrado"
MSG_CAPTCHA_ERROR = "ReCaptcha não foi validado. Verifique e tente novamente."
MSG_CAPTCHA_OK = (
    "Solicitação enviada para processamento. Acompanhe sua execução pela opção 156."
)
MSG_NOT_RELEASED = "Opção não liberada"


async def captcha_handler(page: Page, data_sitekey: str) -> Optional[Page]:
    """
    Solves ReCaptcha in a loop until successful, then returns the screen 156 popup.
    """
    while True:
        # response = Recaptcha().solve_recaptcha(data_sitekey)
        # await page.evaluate(
        #     f"document.getElementById('g-recaptcha-response').innerHTML = '{response}'"
        # )
        await page.wait_for_timeout(500)
        await page.get_by_role("link", name=SEL_BTN_SUBMIT).click()
        await page.wait_for_timeout(500)

        status_message = await page.locator(SEL_ERROR_LABEL).inner_text()

        if MSG_CAPTCHA_ERROR in status_message:
            await page.locator(f"text={SEL_BTN_OK}").click()
            await page.wait_for_timeout(1000)

        elif MSG_CAPTCHA_OK in status_message:
            async with page.expect_popup() as page2_info:
                await page.get_by_role("link", name=SEL_BTN_OPEN_156).click()
            return await page2_info.value


async def _process_date(
    page: Page, domain: str, date: str, attempt: int, max_attempts: int
) -> None:
    """
    Fills the form for a single date, waits for the system response, and
    routes accordingly — the response is always checked for an error modal
    ("Nenhum registro encontrado") before clicking "Abrir a opção 156 agora",
    since not every date is routed to screen 156. On screen 156,
    process_156_report() downloads the file, checks its columns and uploads it.

    Raises:
        TimeoutError: When no message appears within 60 s.
        RuntimeError: For truly unexpected messages.
    """
    await page.locator(SEL_DATE_START).fill(date)
    await page.wait_for_timeout(250)
    await page.locator(SEL_DATE_END).fill(date)
    await page.wait_for_timeout(500)

    # This screen never downloads directly: the outcome is always a message
    # (no records, routed to 156, or unexpected), so only the label is awaited.
    task_msg = asyncio.create_task(
        page.locator(SEL_ERROR_LABEL).wait_for(state="visible")
    )

    await page.get_by_role("link", name=SEL_BTN_SUBMIT).click()

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
        raise TimeoutError("Timeout waiting for system response.")

    text = await page.locator(SEL_ERROR_LABEL).inner_text()

    # ------------------------------------------------------------------
    # SCENARIO 2 — No records for this date
    # ------------------------------------------------------------------
    if MSG_NO_RECORDS in text:
        await page.get_by_role("link", name=SEL_BTN_OK).click()
        log.log(
            level=logging.WARNING,
            domain=domain,
            message=f"{domain} ROUTINE {ROUTINE} | [{date}] {text}",
        )
        return

    # ------------------------------------------------------------------
    # SCENARIO 3 — Routed to screen 156
    # ------------------------------------------------------------------
    if MSG_156 in text or MSG_PROCESSING in text:
        # Captcha path (currently commented out — kept available)
        # data_sitekey = await page.locator(SEL_CAPTCHA).get_attribute("data-sitekey") or ""
        # page2 = await captcha_handler(page, data_sitekey)

        async with page.expect_popup() as page2_info:
            await page.get_by_role("link", name=SEL_BTN_OPEN_156).click()
        page2 = await page2_info.value
        await page2.wait_for_load_state("load")

        if page2 is not None:
            await process_156_report(
                page2, date, ROUTINE, domain, attempt, max_attempts
            )
            # NOTE: page2 is closed inside process_156_report's finally block.
            log.log(
                level=logging.INFO,
                domain=domain,
                message=(
                    f"{domain} ROUTINE {ROUTINE} | "
                    f"[{date}] Processed via screen 156."
                ),
            )
        return

    # ------------------------------------------------------------------
    # SCENARIO 4 — Unexpected message
    # ------------------------------------------------------------------
    await page.get_by_role("link", name=SEL_BTN_OK).click()
    raise RuntimeError(f"Unexpected message: {text}")


def _build_dates_to_process() -> List[str]:
    """
    Fixed dates for this routine: yesterday and today, each processed
    separately — always 2 dates, every run.

    NOTE: no day/hour gate, and not part of the "previous + current month"
    group (bot_002.py and siblings): 930 is a per-day report, and the user
    asked on 2026-09-27 for it to always refresh the last two days
    (yesterday + today) instead of the old hour-based gate (two previous
    days at 1h/12h, today otherwise). Same dates as bot_458.py. Wraps
    Dates().yesterday_and_today() one-to-one.
    """
    return Dates().yesterday_and_today()


async def handler_download(
    page: Page, domain: str, attempt: int, max_attempts: int
) -> None:
    """
    Runs _process_date() once per date from _build_dates_to_process()
    (yesterday + today).

    Each date is isolated — a failure in one does not stop the next — but
    if every date fails the error is re-raised so main()'s retry loop
    still engages.

    Raises:
        RuntimeError: When all dates failed.
    """
    dates_to_process: List[str] = _build_dates_to_process()
    failures: int = 0

    for date in dates_to_process:
        try:
            await _process_date(page, domain, date, attempt, max_attempts)
        except Exception as e_date:
            failures += 1
            log.log(
                level=logging.ERROR,
                domain=domain,
                message=f"{domain} ROUTINE {ROUTINE} | Failed to process [{date}]: {e_date}",
            )

    if failures == len(dates_to_process):
        raise RuntimeError(
            f"{domain} ROUTINE {ROUTINE} | All {len(dates_to_process)} date(s) failed."
        )


async def run(playwright: Playwright, attempt: int, max_attempts: int) -> None:
    """
    Launches the browser, logs in, navigates to routine 930, races the
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
