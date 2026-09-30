import asyncio
import logging
import shutil
import zipfile

from pathlib import Path
from typing import List, Optional, Tuple

from playwright.async_api import Playwright, Page, async_playwright

from config.settings.variables import settings
from app.core.column_sentinel import check_file, has_baseline
from app.core.login import perform_login
from app.core.upload_to_sharepoint import upload_report_file, upload_log_file
from app.utils.cleanup import GarbageCollector
from app.utils.datetime_utils import Dates
from app.utils.custom_logger import get_logger

cwd: Path = Path.cwd()
log = get_logger()
ROUTINE: str = "073"

# ---------------------------------------------------------------------------
# Selectors
# ---------------------------------------------------------------------------
SEL_DATE_START = "#per_ini_inc"
SEL_DATE_END = "#per_fin_inc"
SEL_BTN_SUBMIT = "Arquivo\xa0Excel"  # non-breaking space is intentional
SEL_ROUTINE_INPUT = '[id="\\33 "]'
SEL_ERROR_LABEL = "#errormsglabel"
SEL_BTN_OK = "7. OK"

# ---------------------------------------------------------------------------
# Known business messages
# ---------------------------------------------------------------------------
MSG_NO_RECORDS = "Nenhum registro encontrado"
MSG_NOT_RELEASED = "Opção não liberada"
MSG_SUCCESS = "Arquivo(s) gerado(s) com sucesso."


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
    Processes one date range: fills the form, races download vs. message,
    and uploads the result (one csv per report type, extracted from the
    zip, each checked by the column sentinel). "No records" is logged as
    WARNING and returns normally; any failure raises, and handler_download()
    counts it.

    Possible scenarios:

        1. Arquivo Excel starts a direct download — a .zip containing
           both the FROTA and TERCEIROS csv files, extracted and split
           by _extract_and_upload_zip() (see _process_download()).
        2. An "Aviso: Arquivo(s) gerado(s) com sucesso." message appears
           first — not an error, the zip download starts on its own right
           after, so the modal is dismissed and task_download is awaited.
        3. No records are available.
        4. An unexpected error is displayed.

    NOTE: SSW used to show separate "Baixar arquivo de veículos
    terceiros"/"...frota" links here instead of zipping both files
    together — that scenario is disabled (not deleted) in this file in
    case SSW reverts to it; see the DISABLED blocks above
    _match_zip_entry_type().
    """

    # ------------------------------------------------------------------
    # DISABLED (kept for reference in case SSW reverts to this model).
    #
    # SSW used to show these two links instead of the direct download
    # (numeric prefix omitted here because SSW can display "1. Baixar
    # arquivo..." / "2. Baixar arquivo..." depending on which files are
    # available). As of 2026-09, "Arquivo Excel" instead shows an "Aviso:
    # Arquivo(s) gerado(s) com sucesso." message and delivers a single
    # .zip download containing both the FROTA and TERCEIROS csv files
    # (see _extract_and_upload_zip() below).
    #
    # actions = [
    #     (
    #         "Baixar arquivo de veículos terceiros",
    #         f"{ROUTINE}-TERCEIROS",
    #     ),
    #     (
    #         "Baixar arquivo de veículos frota",
    #         f"{ROUTINE}-FROTA",
    #     ),
    # ]
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Save and upload a downloaded file
    # ------------------------------------------------------------------

    async def _save_and_upload(
        download,
        routine_name: str,
    ) -> None:

        file_name = (
            f"20{dates[0][-2:]}-" f"{dates[0][2:4]}-" f"{domain}-" f"{routine_name}.csv"
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
        if has_baseline(routine_name):
            check_file(path_download / file_name, routine_name, domain)

    # ------------------------------------------------------------------
    # DISABLED (kept for reference in case SSW reverts to this model).
    # See the "actions" note above _save_and_upload() for context.
    #
    # def _normalize_link_text(text: str) -> str:
    #     # Removes numeric prefix from visible link text, e.g.
    #     # "1. Baixar arquivo de veículos frota" ->
    #     # "Baixar arquivo de veículos frota"
    #     text = text.strip()
    #     if ". " in text:
    #         prefix, remainder = text.split(". ", 1)
    #         if prefix.strip().isdigit():
    #             return remainder.strip()
    #     return text
    #
    # async def _find_download_link(expected_text: str):
    #     # Finds a download link regardless of numeric prefix.
    #     links = page.get_by_role("link")
    #     count = await links.count()
    #     for index in range(count):
    #         locator = links.nth(index)
    #         try:
    #             text = await locator.inner_text()
    #         except Exception:
    #             continue
    #         normalized_text = _normalize_link_text(text)
    #         if normalized_text != expected_text:
    #             continue
    #         try:
    #             if await locator.is_visible():
    #                 return locator
    #         except Exception:
    #             continue
    #     return None
    #
    # async def _wait_for_any_download_link(timeout_seconds: float = 60.0) -> bool:
    #     # Waits until at least one configured download link appears.
    #     loop = asyncio.get_running_loop()
    #     deadline = loop.time() + timeout_seconds
    #     while loop.time() < deadline:
    #         for expected_text, _ in actions:
    #             locator = await _find_download_link(expected_text)
    #             if locator is not None:
    #                 return True
    #         await asyncio.sleep(0.20)
    #     return False
    #
    # async def _download_available_actions() -> int:
    #     # Downloads every available configured link.
    #     downloaded_count = 0
    #     await page.wait_for_timeout(250)
    #     for expected_text, routine_name in actions:
    #         locator = await _find_download_link(expected_text)
    #         if locator is None:
    #             log.log(
    #                 level=logging.WARNING,
    #                 domain=domain,
    #                 message=(
    #                     f"{domain} ROUTINE {ROUTINE} | "
    #                     f"Dates: [{dates[0]} → {dates[1]}] | "
    #                     f"Download option not available: "
    #                     f"{expected_text}"
    #                 ),
    #             )
    #             continue
    #         log.log(
    #             level=logging.INFO,
    #             domain=domain,
    #             message=(
    #                 f"{domain} ROUTINE {ROUTINE} | "
    #                 f"Dates: [{dates[0]} → {dates[1]}] | "
    #                 f"Download option found: "
    #                 f"{expected_text}"
    #             ),
    #         )
    #         async with page.expect_download(timeout=120_000) as download_info:
    #             await locator.click()
    #         download = await download_info.value
    #         await page.wait_for_timeout(500)
    #         await _save_and_upload(download, routine_name)
    #         downloaded_count += 1
    #     return downloaded_count
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Match a filename inside the zip to its report type
    #
    # SSW zips the two csvs under a randomly generated prefix (e.g.
    # "alexsant113233_frota.csv" / "alexsant113200_terceiro.csv") — only
    # the fixed "frota"/"terceiro" suffix before the extension is stable,
    # so matching is done by substring instead of an exact name.
    # ------------------------------------------------------------------

    def _match_zip_entry_type(filename: str) -> Optional[str]:

        lowered = filename.lower()

        if "frota" in lowered:
            return f"{ROUTINE}-FROTA"

        if "terceiro" in lowered:
            return f"{ROUTINE}-TERCEIROS"

        return None

    # ------------------------------------------------------------------
    # Save the zip delivered by "Arquivo Excel", extract the FROTA/
    # TERCEIROS csv files inside it, rename each to the routine's
    # standard naming, and upload both to SharePoint.
    # ------------------------------------------------------------------

    async def _extract_and_upload_zip(download) -> int:

        path_download = cwd / "output" / "downloads" / ROUTINE

        path_download.mkdir(parents=True, exist_ok=True)

        zip_path = path_download / f"{ROUTINE}-download.zip"

        await download.save_as(zip_path)

        uploaded_count = 0

        with zipfile.ZipFile(zip_path) as archive:

            for entry_name in archive.namelist():

                routine_name = _match_zip_entry_type(entry_name)

                if routine_name is None:

                    log.log(
                        level=logging.WARNING,
                        domain=domain,
                        message=(
                            f"{domain} ROUTINE {ROUTINE} | "
                            f"Dates: [{dates[0]} → {dates[1]}] | "
                            f"Unrecognized file inside zip, skipped: {entry_name}"
                        ),
                    )

                    continue

                file_name = (
                    f"20{dates[0][-2:]}-"
                    f"{dates[0][2:4]}-"
                    f"{domain}-"
                    f"{routine_name}.csv"
                )

                with (
                    archive.open(entry_name) as source,
                    open(path_download / file_name, "wb") as target,
                ):
                    shutil.copyfileobj(source, target)

                await upload_report_file(
                    file_name=file_name,
                    routine=ROUTINE,
                    domain=domain,
                    attempt=attempt,
                    max_attempts=max_attempts,
                )
                # Each csv has its own baseline: 073-FROTA.yml / 073-TERCEIROS.yml
                if has_baseline(routine_name):
                    check_file(path_download / file_name, routine_name, domain)

                uploaded_count += 1

        zip_path.unlink(missing_ok=True)

        return uploaded_count

    # ------------------------------------------------------------------
    # Dispatch a completed download to the zip flow, or to the plain
    # single-csv flow as a defensive fallback for the pre-zip behavior
    # (a combined report with no FROTA/TERCEIROS split).
    # ------------------------------------------------------------------

    async def _process_download(download) -> None:

        suggested_name = (download.suggested_filename or "").lower()

        if suggested_name.endswith(".zip"):

            uploaded_count = await _extract_and_upload_zip(download)

            if uploaded_count == 0:

                raise RuntimeError(
                    "Zip download had no recognizable FROTA/TERCEIROS file inside."
                )

            return

        await _save_and_upload(download, ROUTINE)

    # ------------------------------------------------------------------
    # Dismiss the "Aviso: Arquivo(s) gerado(s) com sucesso." modal if it
    # is still open.
    #
    # The download can fire before the modal finishes rendering (winning
    # the race in Scenario 2), in which case the modal is left on screen
    # unclosed — it then blocks filling the next date range's form, so
    # every path that finishes processing a download checks for it here
    # instead of assuming Scenario 3 already handled the click.
    # ------------------------------------------------------------------

    async def _dismiss_success_modal_if_present() -> None:

        try:
            await page.locator(SEL_ERROR_LABEL).wait_for(state="visible", timeout=5_000)

        except Exception:
            return

        ok_btn = page.get_by_role("link", name=SEL_BTN_OK)

        try:

            if await ok_btn.is_visible():

                await ok_btn.click()

                await page.wait_for_timeout(500)

        except Exception:
            pass

    # ------------------------------------------------------------------
    # Fill report form
    # ------------------------------------------------------------------

    await page.wait_for_timeout(250)

    await page.locator(SEL_DATE_START).focus()

    await page.wait_for_timeout(250)

    await page.locator(SEL_DATE_START).fill(dates[0])

    await page.wait_for_timeout(250)

    await page.locator(SEL_DATE_START).press("Tab")

    await page.locator(SEL_DATE_END).fill(dates[1])

    await page.wait_for_timeout(250)

    # ------------------------------------------------------------------
    # Start listeners BEFORE clicking Arquivo Excel
    #
    # Two things can happen:
    #
    # 1. Direct download of the .zip (FROTA + TERCEIROS bundled inside)
    # 2. An "Aviso" message appears — either the report-generated success
    #    message (MSG_SUCCESS, in which case the zip download still starts
    #    on its own right after) or a real message (no records / unexpected)
    #
    # The old per-category link scenario is disabled — see the DISABLED
    # block above _match_zip_entry_type() — so task_actions is not raced
    # here anymore.
    # ------------------------------------------------------------------

    task_download = asyncio.create_task(page.wait_for_event("download"))

    task_error = asyncio.create_task(
        page.locator(SEL_ERROR_LABEL).wait_for(state="visible")
    )

    await page.wait_for_timeout(250)

    # ------------------------------------------------------------------
    # Click Arquivo Excel
    # ------------------------------------------------------------------

    await page.get_by_role("link", name=SEL_BTN_SUBMIT).click()

    # ------------------------------------------------------------------
    # Wait for first result
    # ------------------------------------------------------------------

    done, pending = await asyncio.wait(
        [
            task_download,
            task_error,
        ],
        timeout=60.0,
        return_when=asyncio.FIRST_COMPLETED,
    )

    # ------------------------------------------------------------------
    # Scenario 1 — Nothing happened
    # ------------------------------------------------------------------

    if not done:

        for task in pending:
            task.cancel()

        await asyncio.gather(*pending, return_exceptions=True)

        raise TimeoutError("Timeout: no download or message appeared.")

    # ------------------------------------------------------------------
    # Scenario 2 — Direct download (the .zip, normally)
    # ------------------------------------------------------------------

    if task_download in done and not task_download.exception():

        for task in pending:
            task.cancel()

        await asyncio.gather(*pending, return_exceptions=True)

        download = task_download.result()

        await page.wait_for_timeout(500)

        await _process_download(download)

        await _dismiss_success_modal_if_present()

        return

    # ------------------------------------------------------------------
    # Scenario 3 — Message appeared
    # ------------------------------------------------------------------

    if task_error in done and not task_error.exception():

        text: str = await page.locator(SEL_ERROR_LABEL).inner_text()

        text = text.strip()

        # --------------------------------------------------------------
        # "Arquivo(s) gerado(s) com sucesso." is not an error — it just
        # confirms the report was generated. The zip download already
        # starts on its own once this message appears, so dismiss the
        # modal and keep waiting on task_download instead of cancelling
        # it.
        # --------------------------------------------------------------

        if MSG_SUCCESS in text:

            ok_btn = page.get_by_role("link", name=SEL_BTN_OK)

            try:

                if await ok_btn.is_visible():
                    await ok_btn.click()

            except Exception:
                pass

            if task_download.done():
                download = task_download.result()

            else:

                try:
                    download = await asyncio.wait_for(task_download, timeout=60.0)

                except asyncio.TimeoutError:

                    task_download.cancel()

                    await asyncio.gather(task_download, return_exceptions=True)

                    raise TimeoutError(
                        "Timeout: success message appeared but the zip download never started."
                    )

            await page.wait_for_timeout(500)

            await _process_download(download)

            await _dismiss_success_modal_if_present()

            return

        # --------------------------------------------------------------
        # No records
        # --------------------------------------------------------------

        if MSG_NO_RECORDS in text:

            ok_btn = page.get_by_role("link", name=SEL_BTN_OK)

            try:

                if await ok_btn.is_visible():

                    await ok_btn.click()

                    await page.wait_for_timeout(500)

            except Exception:
                pass

            if not task_download.done():
                task_download.cancel()

            await asyncio.gather(task_download, return_exceptions=True)

            log.log(
                level=logging.WARNING,
                domain=domain,
                message=(
                    f"{domain} ROUTINE {ROUTINE} | "
                    f"Dates: [{dates[0]} → {dates[1]}] | "
                    f"{text}"
                ),
            )

            return

        # --------------------------------------------------------------
        # Unexpected message
        # --------------------------------------------------------------

        ok_btn = page.get_by_role("link", name=SEL_BTN_OK)

        try:

            if await ok_btn.is_visible():

                await ok_btn.click()

                await page.wait_for_timeout(500)

        except Exception:
            pass

        if not task_download.done():
            task_download.cancel()

        await asyncio.gather(task_download, return_exceptions=True)

        raise RuntimeError(f"Unexpected error message: {text}")

    # ------------------------------------------------------------------
    # Fallback
    # ------------------------------------------------------------------

    for task in pending:
        task.cancel()

    await asyncio.gather(*pending, return_exceptions=True)

    raise RuntimeError("Unexpected state while processing report.")


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
    Launches the browser, logs in, navigates to routine 073, races the
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

        await page.wait_for_timeout(500)

        await page.locator(SEL_ROUTINE_INPUT).fill(ROUTINE)

        await page.wait_for_timeout(250)

        # Race popup vs. menu-level error label instead of assuming the popup
        # always opens — a routine that is "Opção não liberada" for this
        # domain never opens a popup, only the error label.
        task_popup = asyncio.create_task(page.wait_for_event("popup"))

        task_error = asyncio.create_task(
            page.locator(SEL_ERROR_LABEL).wait_for(state="visible")
        )

        await page.wait_for_timeout(250)

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
                f"ROUTINE {ROUTINE} | "
                f"Timeout: neither popup nor error "
                f"appeared after pressing Tab."
            )

        if task_error in done and not task_error.exception():

            text = await page.locator(SEL_ERROR_LABEL).inner_text()

            if text:

                await page.get_by_role("link", name=SEL_BTN_OK).click()

                error_msg = f"ROUTINE {ROUTINE} | " f"{text}"

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
                f"{settings.ssw_domain} "
                f"ROUTINE {ROUTINE} | "
                f"{e} - "
                f"attempt {attempt}/{max_attempts}."
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
                                f"{settings.ssw_domain} "
                                f"ROUTINE {ROUTINE} | "
                                f"{error_msg}. "
                                f"Aborting attempts."
                            ),
                        )

                        break

                    if attempt == MAX_ATTEMPTS:

                        log.log(
                            level=logging.ERROR,
                            domain=settings.ssw_domain,
                            message=(
                                f"{settings.ssw_domain} "
                                f"ROUTINE {ROUTINE} | "
                                f"MAXIMUM ATTEMPTS REACHED. "
                                f"Ending routine."
                            ),
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
