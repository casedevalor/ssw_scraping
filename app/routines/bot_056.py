import asyncio
import logging

from pathlib import Path
from datetime import datetime
from typing import List

from playwright.async_api import Playwright, async_playwright, Page

from config.settings.variables import settings
from app.core.login import perform_login
from app.core.upload_to_sharepoint import upload_report_file, upload_log_file
from app.utils.cleanup import GarbageCollector
from app.core.data_processing import Dataframe_Analytics
from app.utils.custom_logger import get_logger

cwd: Path = Path.cwd()
log = get_logger()
ROUTINE: str = "056"

# ---------------------------------------------------------------------------
# Selectors
# ---------------------------------------------------------------------------
SEL_ROUTINE_INPUT = '[id="\\33 "]'
SEL_LINK_CODIGO = "Código"
SEL_ERROR_LABEL = "#errormsglabel"
SEL_BTN_OK = "7. OK"

# ---------------------------------------------------------------------------
# Known business messages
# ---------------------------------------------------------------------------
MSG_NOT_RELEASED = "Opção não liberada"


async def handler_download(
    page: Page, domain: str, attempt: int, max_attempts: int
) -> None:
    """
    Fills report parameters, triggers download for each configured report,
    processes the SSWWEB file, and uploads to SharePoint.

    Each report is isolated — a failure in one does not stop the others.
    """
    await asyncio.sleep(0.5)

    # Click the "Código" link to sort or view the available reports
    await page.get_by_role("link", name=SEL_LINK_CODIGO).click()
    await page.wait_for_timeout(500)

    # Get the list of reports to process from settings safely
    reports: List[str] = getattr(
        settings.yaml_config.variables.bot_056, "number_reports", ["020", "023"]
    )

    for report in reports:
        try:
            report_link = page.get_by_role("link", name=f"{report}")
            if not await report_link.is_visible():
                log.log(
                    level=logging.WARNING,
                    domain=domain,
                    message=f"{domain} ROUTINE {ROUTINE} | Report '{report}' not found on the page.",
                )
                continue

            # Race the download against a possible error modal, instead of
            # blindly waiting out expect_download()'s own timeout on failure.
            task_download = asyncio.create_task(page.wait_for_event("download"))
            task_msg = asyncio.create_task(
                page.locator(SEL_ERROR_LABEL).wait_for(state="visible")
            )
            await report_link.click()

            done, pending = await asyncio.wait(
                [task_download, task_msg],
                timeout=120.0,
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)

            if not done:
                log.log(
                    level=logging.ERROR,
                    domain=domain,
                    message=f"{domain} ROUTINE {ROUTINE} | Timeout waiting for report '{report}' to download.",
                )
                continue

            if task_msg in done and not task_msg.exception():
                text = await page.locator(SEL_ERROR_LABEL).inner_text()
                await page.get_by_role("link", name=SEL_BTN_OK).click()
                log.log(
                    level=logging.WARNING,
                    domain=domain,
                    message=f"{domain} ROUTINE {ROUTINE} | Report '{report}': {text}",
                )
                continue

            if task_download.exception():
                # Propagate the real Playwright error instead of masking it.
                raise task_download.exception()
            download = task_download.result()

            # Save the file temporarily
            file_name = f"{datetime.today().strftime('%Y-%m-%d')}-{domain}-{ROUTINE}-{report}.sswweb"
            path_download = cwd / "output" / "downloads" / ROUTINE
            path_download.mkdir(parents=True, exist_ok=True)
            full_path = path_download / file_name
            await download.save_as(full_path)

            # Process the file via Dataframe_Analytics in a separate thread (Non-blocking)
            loop = asyncio.get_event_loop()
            processed_path = await loop.run_in_executor(
                None,
                lambda r=report, p=str(
                    full_path
                ): Dataframe_Analytics().processing_sswweb_056(file_name=p, report=r),
            )
            processed_file_name = Path(processed_path).name

            # Upload to SharePoint
            await upload_report_file(
                file_name=processed_file_name,
                routine=ROUTINE,
                domain=domain,
                attempt=attempt,
                max_attempts=max_attempts,
                report=report,
            )

            log.log(
                level=logging.INFO,
                domain=domain,
                message=(
                    f"{domain} ROUTINE {ROUTINE} | "
                    f"Report '{report}' downloaded, processed, and uploaded successfully."
                ),
            )

        except Exception as e_report:
            log.log(
                level=logging.ERROR,
                domain=domain,
                message=(
                    f"{domain} ROUTINE {ROUTINE} | "
                    f"Failed to process report '{report}': {e_report}"
                ),
            )
            continue


async def run(playwright: Playwright, attempt: int, max_attempts: int) -> None:
    """
    Launches the browser, logs in, navigates to routine 056, races the
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
            page_routine = task_popup.result()
            await page_routine.wait_for_load_state("load")
            await handler_download(
                page_routine, settings.ssw_domain, attempt, max_attempts
            )

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
