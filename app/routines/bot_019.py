import asyncio
import logging

from pathlib import Path
from typing import List

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
ROUTINE: str = "019"

# ---------------------------------------------------------------------------
# Selectors
# ---------------------------------------------------------------------------
SEL_UNIT_INPUT = '[id="\\32 "]'
SEL_ROUTINE_INPUT = '[id="\\33 "]'
SEL_EXCEL_FIELD = "#relatorio_excel"
SEL_BTN_SUBMIT = "►"
SEL_ERROR_LABEL = (
    "#errormsglabel"  # reused id on both the main menu page and the report popup
)
SEL_BTN_OK = "7. OK"
SEL_BTN_OPEN_156 = "1. Abrir a opção 156 agora"

# ---------------------------------------------------------------------------
# Known business messages
# ---------------------------------------------------------------------------
MSG_BRANCH_NOT_FOUND = (
    "Filial não encontrada."  # menu-level, right after Tab on the routine number
)
MSG_NO_DATA = "Não selecionou dados para os parâmetros informados"
MSG_UNIT_NOT_REGISTERED = (
    "Sigla da unidade centralizadora não cadastrada."  # popup-level, on report submit
)
MSG_156_QUEUED = (
    "Solicitação enviada para processamento. Acompanhe sua execução pela opção 156."
)
MSG_NOT_RELEASED = "Opção não liberada"


async def handler_download(
    page: Page, domain: str, unit: str, attempt: int, max_attempts: int
) -> None:
    """
    Fills the report format field for a single unit and submits.

    Races the download against the known message modal instead of probing
    first and clicking again — this screen shows no message at all when the
    report is ready, so a message-only wait would burn the full timeout on
    every successful unit before ever starting the real download.
    """
    await page.locator(SEL_EXCEL_FIELD).click()
    await page.locator(SEL_EXCEL_FIELD).fill("S")
    await page.wait_for_timeout(250)

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

    # Cancel the losing task AND await it to suppress "Task exception was
    # never retrieved" from Playwright's internal timeout.
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)

    # ------------------------------------------------------------------
    # SCENARIO 1 — Timeout: neither the download nor a message showed up
    # ------------------------------------------------------------------
    if not done:
        log.log(
            level=logging.ERROR,
            domain=domain,
            message=f"{domain} ROUTINE {ROUTINE} | Unit {unit}: Timeout, neither download nor message appeared.",
        )
        return

    # ------------------------------------------------------------------
    # SCENARIO 2 — Download fired directly off the first click
    # ------------------------------------------------------------------
    if task_download in done and not task_download.exception():
        download = task_download.result()

        file_name = f"{domain}-{unit}-{ROUTINE}.csv"
        path_download = cwd / "output" / "downloads" / ROUTINE
        path_download.mkdir(parents=True, exist_ok=True)
        await download.save_as(path_download / file_name)
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
            message=f"{domain} ROUTINE {ROUTINE} | Unit {unit}: Download and upload successful.",
        )
        return

    # ------------------------------------------------------------------
    # SCENARIO 3 — A message modal appeared instead
    # ------------------------------------------------------------------
    text = await page.locator(SEL_ERROR_LABEL).inner_text()

    if MSG_156_QUEUED in text:
        async with page.expect_popup() as page2_info:
            await page.get_by_role("link", name=SEL_BTN_OPEN_156).click()
        page2 = await page2_info.value
        await page2.wait_for_load_state("load")

        await process_156_report(
            page2, Dates().current_day(), ROUTINE, domain, attempt, max_attempts, unit
        )
        log.log(
            level=logging.INFO,
            domain=domain,
            message=f"{domain} ROUTINE {ROUTINE} | Unit {unit}: Processed via screen 156.",
        )
        return

    if MSG_NO_DATA in text:
        await page.get_by_role("link", name=SEL_BTN_OK).click()
        log.log(
            level=logging.WARNING,
            domain=domain,
            message=f"{domain} ROUTINE {ROUTINE} | Unit {unit}: {text}",
        )
        return

    if MSG_UNIT_NOT_REGISTERED in text:
        await page.get_by_role("link", name=SEL_BTN_OK).click()
        log.log(
            level=logging.WARNING,
            domain=domain,
            message=f"{domain} ROUTINE {ROUTINE} | Unit {unit}: {text}",
        )
        return

    # Any other message is unrecognized — dismissed the same way, no
    # fallback retry needed since the download was already raced above.
    await page.get_by_role("link", name=SEL_BTN_OK).click()
    log.log(
        level=logging.WARNING,
        domain=domain,
        message=f"{domain} ROUTINE {ROUTINE} | Unit {unit}: Unrecognized message: {text}",
    )


async def run(playwright: Playwright, attempt: int, max_attempts: int) -> None:
    """
    Launches the browser, logs in, and runs routine 019 once per configured unit.

    A fresh popup is opened for every unit (the unit + routine number are
    re-entered each time), so each popup is explicitly closed after use to
    avoid leaving dozens of tabs open across a full unit list. Unit-level
    failures (including menu-level errors such as MSG_NOT_RELEASED) are
    logged and skipped, so they don't reach main()'s retry loop; only
    browser/login failures are re-raised.
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

        list_units_019: List[str] = settings.yaml_config.variables.bot_019.list_units

        for unit in list_units_019:
            try:
                await page.locator(SEL_UNIT_INPUT).click()
                await page.locator(SEL_UNIT_INPUT).fill(unit)
                await page.wait_for_timeout(250)
                await page.locator(SEL_ROUTINE_INPUT).click()
                await page.locator(SEL_ROUTINE_INPUT).fill(ROUTINE)
                await page.wait_for_timeout(250)

                # Race popup vs. menu-level error label instead of assuming the
                # popup always opens — a routine that is "Opção não liberada"
                # (or a unit that is "Filial não encontrada") never opens a
                # popup, only the error label. Handled per unit below.
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
                    log.log(
                        level=logging.ERROR,
                        domain=settings.ssw_domain,
                        message=f"{settings.ssw_domain} ROUTINE {ROUTINE} | Unit {unit}: Timeout, neither popup nor error appeared.",
                    )
                    continue

                # Menu-level error for this unit: log it and move on to the
                # next unit instead of aborting the whole run.
                if task_error in done and not task_error.exception():
                    text = await page.locator(SEL_ERROR_LABEL).inner_text()
                    await page.get_by_role("link", name=SEL_BTN_OK).click()
                    log.log(
                        level=logging.WARNING,
                        domain=settings.ssw_domain,
                        message=f"{settings.ssw_domain} ROUTINE {ROUTINE} | Unit {unit}: {text}",
                    )
                    continue

                popup_page = task_popup.result()
                await popup_page.wait_for_load_state("load")

                # On this screen SSW can open the popup AND show a
                # menu-level error at (almost) the same time — e.g.
                # "Filial não encontrada" — so the popup winning the race
                # above doesn't guarantee the unit was actually accepted.
                # Give the main page's error label a short grace check
                # before handing off to handler_download.
                error_label = page.locator(SEL_ERROR_LABEL)
                await page.wait_for_timeout(500)
                if await error_label.count() > 0:
                    text = await error_label.inner_text()
                    if text.strip():
                        await page.get_by_role("link", name=SEL_BTN_OK).click()
                        log.log(
                            level=logging.WARNING,
                            domain=settings.ssw_domain,
                            message=f"{settings.ssw_domain} ROUTINE {ROUTINE} | Unit {unit}: {text}",
                        )
                        await popup_page.close()
                        continue

                await handler_download(
                    popup_page, settings.ssw_domain, unit, attempt, max_attempts
                )
                await popup_page.close()

            except Exception as e_unit:
                # Per-unit isolation: one failing unit must not block the rest.
                log.log(
                    level=logging.ERROR,
                    domain=settings.ssw_domain,
                    message=f"{settings.ssw_domain} ROUTINE {ROUTINE} | Failed to process unit {unit}: {e_unit}",
                )
                continue

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
