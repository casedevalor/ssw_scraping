import asyncio
import logging

from pathlib import Path

from playwright.async_api import Playwright, Page, async_playwright

from config.settings.variables import settings
from app.core.column_sentinel import check_file
from app.core.upload_to_sharepoint import upload_report_file, upload_log_file
from app.core.login import perform_login
from app.utils.cleanup import GarbageCollector
from app.utils.custom_logger import get_logger

# Current directory and routine name
cwd: Path = Path.cwd()
log = get_logger()
ROUTINE: str = "081"

# ---------------------------------------------------------------------------
# Selectors — centralized so any future ID change is a one-line fix
# ---------------------------------------------------------------------------
SEL_UNIT_INPUT = '[id="\\32 "]'
SEL_ROUTINE_INPUT = '[id="\\33 "]'
SEL_EXCEL_CHECKBOX = "#relatorio_excel"
SEL_SUBMIT_LINK = "1. Relacionar as entregas,"
SEL_ERROR_LABEL = "#errormsglabel"
SEL_BTN_OK = "7. OK"

# ---------------------------------------------------------------------------
# Known business messages
# ---------------------------------------------------------------------------
MSG_NOT_RELEASED = "Opção não liberada"


async def handler_download(
    page: Page, domain: str, unit: str, attempt: int, max_attempts: int
) -> None:
    """
    Fills out the report popup, races the download against the popup's error
    label, and on success saves the file, runs the column sentinel and uploads
    it to SharePoint. A message instead of a download is dismissed and logged
    as WARNING, not raised.

    :param page: Report popup where the form is filled.
    :param domain: Domain used for file naming.
    :param unit: Current unit being processed.
    :param attempt: Current attempt number.
    :param max_attempts: Maximum number of attempts.

    Raises:
        TimeoutError: When neither a download nor an error message appears within 60 s.
    """

    # Fill out the report form fields
    await page.locator(SEL_EXCEL_CHECKBOX).click()
    await page.locator(SEL_EXCEL_CHECKBOX).fill("S")
    await page.wait_for_timeout(250)

    # Create concurrent tasks: download event OR error label becoming visible
    task_download = asyncio.create_task(page.wait_for_event("download"))
    task_msg = asyncio.create_task(
        page.locator(SEL_ERROR_LABEL).wait_for(state="visible")
    )

    # Trigger the download process
    await page.get_by_role("link", name=SEL_SUBMIT_LINK).click()

    # Wait for the FIRST event to happen — SSW can be slow to generate the file.
    done, pending = await asyncio.wait(
        [task_download, task_msg], timeout=60.0, return_when=asyncio.FIRST_COMPLETED
    )

    # Always cancel the task that lost the race to avoid memory leaks
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)

    # ------------------------------------------------------------------
    # SCENARIO 1 — Global timeout (system hung, no download, no warning)
    # ------------------------------------------------------------------
    if not done:
        raise TimeoutError(
            f"ROUTINE {ROUTINE} | Unit {unit}: "
            "Timeout: no download started and no warning appeared."
        )

    # ------------------------------------------------------------------
    # SCENARIO 2 — Download started successfully
    # ------------------------------------------------------------------
    if task_download in done and not task_download.exception():
        download = task_download.result()
        file_name = f"{domain}-{unit}-{ROUTINE}.csv"
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
            message=f"{domain} ROUTINE {ROUTINE} | Unit {unit}: Download successful.",
        )
        return

    # ------------------------------------------------------------------
    # SCENARIO 3 — A message appeared instead of a download. The specific
    # messages this screen can show are not yet catalogued, so this is a
    # generic dismiss-and-log branch rather than a per-message dispatch.
    # ------------------------------------------------------------------
    if task_msg in done and not task_msg.exception():
        text = await page.locator(SEL_ERROR_LABEL).inner_text()
        await page.get_by_role("link", name=SEL_BTN_OK).click()
        log.log(
            level=logging.WARNING,
            domain=domain,
            message=f"{domain} ROUTINE {ROUTINE} | Unit {unit}: {text}",
        )
        return


async def run(playwright: Playwright, attempt: int, max_attempts: int) -> None:
    """
    Launches the browser, logs in, and runs routine 081 once per configured
    unit. A fresh popup is opened per unit, racing it against the menu-level
    error label (MSG_NOT_RELEASED), then delegates to handler_download().
    Unit-level failures are logged and skipped so one unit can't block the rest.

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

        # Perform login for the specified routine
        await perform_login(page, ROUTINE)
        await page.wait_for_timeout(250)
        # Units come from config.yml → variables.bot_081.list_units
        list_units_081 = settings.yaml_config.variables.bot_081.list_units
        # An empty list (e.g. a misspelled key in config.yml falls back to the
        # model default) would otherwise end the run silently after login.
        if not list_units_081:
            log.log(
                level=logging.WARNING,
                domain=settings.ssw_domain,
                message=f"{settings.ssw_domain} ROUTINE {ROUTINE} | No units configured in config.yml (bot_081.list_units).",
            )

        for unit in list_units_081:
            try:
                await page.locator(SEL_UNIT_INPUT).click()
                await page.locator(SEL_UNIT_INPUT).fill(unit)
                await page.wait_for_timeout(250)
                await page.locator(SEL_ROUTINE_INPUT).click()
                await page.locator(SEL_ROUTINE_INPUT).fill(ROUTINE)

                # Race popup vs. menu-level error label instead of assuming
                # the popup always opens — a routine that is "Opção não
                # liberada" for this domain never opens a popup, only the
                # error label.
                task_popup = asyncio.create_task(page.wait_for_event("popup"))
                task_erro = asyncio.create_task(
                    page.locator(SEL_ERROR_LABEL).wait_for(state="visible")
                )

                await page.locator(SEL_ROUTINE_INPUT).press("Tab")

                done, pending = await asyncio.wait(
                    [task_popup, task_erro],
                    timeout=10.0,
                    return_when=asyncio.FIRST_COMPLETED,
                )

                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)

                if not done:
                    log.log(
                        level=logging.ERROR,
                        domain=settings.ssw_domain,
                        message=f"{settings.ssw_domain} ROUTINE {ROUTINE} | Unit {unit}: Timeout: popup did not open.",
                    )
                    continue

                if task_erro in done and not task_erro.exception():
                    text = await page.locator(SEL_ERROR_LABEL).inner_text()
                    await page.get_by_role("link", name=SEL_BTN_OK).click()
                    log.log(
                        level=logging.WARNING,
                        domain=settings.ssw_domain,
                        message=f"{settings.ssw_domain} ROUTINE {ROUTINE} | Unit {unit}: {text}",
                    )
                    continue

                if task_popup in done and not task_popup.exception():
                    popup_page = task_popup.result()
                    await popup_page.wait_for_load_state("load")
                    await handler_download(
                        popup_page, settings.ssw_domain, unit, attempt, max_attempts
                    )
                    # A new popup opens for every unit — close it so they don't pile up.
                    await popup_page.close()

            except Exception as e_unit:
                # Swallowed on purpose: one failing unit must not block the rest.
                log.log(
                    level=logging.ERROR,
                    domain=settings.ssw_domain,
                    message=f"{settings.ssw_domain} ROUTINE {ROUTINE} | Unit {unit}: {e_unit}",
                )
                continue  # Move to the next unit

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
                            message=f"{settings.ssw_domain} ROUTINE {ROUTINE} | {error_msg}. Aborting attempts.",
                        )
                        break

                    if attempt == MAX_ATTEMPTS:
                        log.log(
                            level=logging.ERROR,
                            domain=settings.ssw_domain,
                            message=f"{settings.ssw_domain} ROUTINE {ROUTINE} | MAXIMUM ATTEMPTS REACHED.",
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
            log.debug(
                message=f"INFO - {ROUTINE} - Sending final log to SharePoint...",
                level="INFO",
            )
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
            log.debug(
                message=f"INFO - {ROUTINE} - Cleaning up downloads...", level="INFO"
            )
            GarbageCollector().delete(cwd / "output" / "downloads" / ROUTINE)
        except Exception as e_gc:
            log.log(
                level=logging.WARNING,
                domain=settings.ssw_domain,
                message=f"WARNING - {ROUTINE} - Failed to clean downloads: {e_gc}",
            )
