import asyncio
import logging

from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from playwright.async_api import BrowserContext, Dialog, Download, Locator, Page, Request
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from app.core.column_sentinel import check_file, has_baseline
from app.core.data_processing import Dataframe_Analytics
from app.core.upload_to_sharepoint import upload_report_file
from app.utils.custom_logger import get_logger


cwd: Path = Path.cwd()
log = get_logger()

# ---------------------------------------------------------------------------
# Selectors — centralized so any future change is a one-line fix
# ---------------------------------------------------------------------------
SEL_ROW_DATA   = "tr.srtr2"
SEL_COL_SIT    = "td:nth-child(7)"   # Situation column  (e.g. "Concluído")
SEL_COL_ACTION = "td:nth-child(9)"   # Action column     (e.g. "Baixar")
SEL_BTN_UPDATE = "Atualizar"

# Alert SSW shows when "Baixar" is clicked while another AJAX call of the
# page is still pending — the click is then ignored.
MSG_BUSY = "Ja existe uma requisicao em andamento"

# ---------------------------------------------------------------------------
# Tuning knobs
# ---------------------------------------------------------------------------
MAX_REFRESH_ATTEMPTS = 120  # 120 × 4 s = 8 min max wait — some companies take longer to release the file
DOWNLOAD_TIMEOUT_MS  = 60_000   # per "Baixar" click — SSW can take over 30 s to start delivering a "Concluído" file
MAX_DOWNLOAD_CLICKS  = 4        # a lost click is retried after a fresh refresh (up to 4 × 60 s)
MAX_BUSY_RETRIES     = 10       # re-clicks on 'Baixar' after the MSG_BUSY alert, each once the page is idle
IDLE_SETTLE_S        = 1.0      # extra pause after the page goes idle, before clicking 'Baixar'
STEP_TIMEOUT_MS      = 10_000   # per refresh click / page load / row read — a stuck step is skipped, not waited on for the context's 120 s


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _build_file_name(
    routine: str,
    date: str,
    domain: str,
    unit: Optional[str] = None,
    suffix: str = "csv",
) -> str:
    """
    Centralizes filename construction so no branch builds its own string.

    Date encoding rules:
        date[-2:]  → year   (e.g. "24" → "2024")
        date[2:4]  → month  (e.g. "01")
        date[:2]   → day    (e.g. "15")
    """
    year  = f"20{date[-2:]}"
    month = date[2:4]
    day   = date[:2]

    if routine == "441":
        return f"{year}-{domain}-{routine}.{suffix}"

    if routine == "477":
        return f"{year}-{domain}-{routine}.{suffix}"

    if routine in ("076", "019"):
        # unit is guaranteed non-None here — validated in _handle_076 / _handle_019
        return f"{year}-{month}-{domain}-{unit}-{routine}.{suffix}"

    if routine == "306":
        file_suffix = "-1" if day == "01" else "-2"
        return f"{year}-{month}-{domain}-{routine}{file_suffix}.{suffix}"

    if routine == "930":
        return f"{year}-{month}-{day}-{domain}-{routine}.{suffix}"

    if routine == "324":
        return f"{year}-{month}-{domain}-{routine}.sswweb"

    # Default: covers 455CGK, 455ABH, 455F, 467, 493, 916 and any future routine
    return f"{year}-{month}-{domain}-{routine}.{suffix}"


async def _save_and_process(
    download: Download,
    file_name: str,
    path_download: Path,
    processor,           # callable or None
) -> str:
    """
    Saves the download to disk, optionally runs a processor over it,
    and returns the final file name (which may change after processing).
    """
    full_path = path_download / file_name
    await download.save_as(full_path)

    if processor is not None:
        processed_path = processor(file_path=str(full_path))
        return Path(processed_path).name

    return file_name


# ---------------------------------------------------------------------------
# Per-routine handlers — one isolated function per routine, dispatched via ROUTINE_HANDLERS
# ---------------------------------------------------------------------------

async def _handle_019(download: Download, date: str, domain: str, path: Path, unit: Optional[str]) -> str:
    if not unit:
        raise ValueError("ROUTINE 019 requires 'unit' but received None.")
    file_name = _build_file_name("019", date, domain, unit=unit)
    return await _save_and_process(download, file_name, path, processor=None)

async def _handle_076(download: Download, date: str, domain: str, path: Path, unit: Optional[str]) -> str:
    # Validate unit early — fail fast with a clear message
    if not unit:
        raise ValueError("ROUTINE 076 requires 'unit' but received None.")
    file_name = _build_file_name("076", date, domain, unit=unit)
    return await _save_and_process(download, file_name, path, processor=None)

async def _handle_306(download: Download, date: str, domain: str, path: Path, unit: Optional[str]) -> str:
    file_name = _build_file_name("306", date, domain)
    return await _save_and_process(download, file_name, path, processor=None)

async def _handle_324(download: Download, date: str, domain: str, path: Path, unit: Optional[str]) -> str:
    file_name = _build_file_name("324", date, domain)
    return await _save_and_process(download, file_name, path, Dataframe_Analytics().processing_sswweb_324)

async def _handle_441(download: Download, date: str, domain: str, path: Path, unit: Optional[str]) -> str:
    file_name = _build_file_name("441", date, domain)
    return await _save_and_process(download, file_name, path, Dataframe_Analytics().processing_csv_441)

async def _handle_455ABH(download: Download, date: str, domain: str, path: Path, unit: Optional[str]) -> str:
    file_name = _build_file_name("455ABH", date, domain)
    return await _save_and_process(download, file_name, path, Dataframe_Analytics().processing_csv_455ABH)

async def _handle_455CGK(download: Download, date: str, domain: str, path: Path, unit: Optional[str]) -> str:
    file_name = _build_file_name("455CGK", date, domain)
    return await _save_and_process(download, file_name, path, Dataframe_Analytics().processing_csv_455CGK)

async def _handle_455F(download: Download, date: str, domain: str, path: Path, unit: Optional[str]) -> str:
    file_name = _build_file_name("455F", date, domain)
    return await _save_and_process(download, file_name, path, processor=None)

async def _handle_467(download: Download, date: str, domain: str, path: Path, unit: Optional[str]) -> str:
    file_name = _build_file_name("467", date, domain)
    return await _save_and_process(download, file_name, path, processor=None)

async def _handle_477(download: Download, date: str, domain: str, path: Path, unit: Optional[str]) -> str:
    file_name = _build_file_name("477", date, domain)
    return await _save_and_process(download, file_name, path, processor=None)

async def _handle_493(download: Download, date: str, domain: str, path: Path, unit: Optional[str]) -> str:
    file_name = _build_file_name("493", date, domain)
    return await _save_and_process(download, file_name, path, processor=None)

async def _handle_916(download: Download, date: str, domain: str, path: Path, unit: Optional[str]) -> str:
    file_name = _build_file_name("916", date, domain)
    return await _save_and_process(download, file_name, path, processor=None)

async def _handle_930(download: Download, date: str, domain: str, path: Path, unit: Optional[str]) -> str:
    file_name = _build_file_name("930", date, domain)
    return await _save_and_process(download, file_name, path, processor=None)

async def _handle_default(download: Download, date: str, domain: str, path: Path, unit: Optional[str]) -> str:
    """Fallback for any routine not explicitly listed above."""
    file_name = _build_file_name("", date, domain)
    return await _save_and_process(download, file_name, path, processor=None)


# Dispatch table — adding a new routine = one new function + one line here.
# A routine missing from it silently falls through to _handle_default.
ROUTINE_HANDLERS = {
    "019":    _handle_019,
    "076":    _handle_076,
    "306":    _handle_306,
    "324":    _handle_324,
    "441":    _handle_441,
    "455ABH": _handle_455ABH,
    "455CGK": _handle_455CGK,
    "455F":   _handle_455F,
    "467":    _handle_467,
    "477":    _handle_477,
    "493":    _handle_493,
    "916":    _handle_916,
    "930":    _handle_930,
}

# Baselines checked per routine. By default it's the one named after the
# routine (config/sentinel/<routine>.yml); a file with several header bands
# has one baseline per band, all checked against the same file.
SENTINEL_BASELINES: Dict[str, Tuple[str, ...]] = {
    "916": ("916-MANIFESTO", "916-CTRC"),
}


async def _refresh(page: Page) -> None:
    """
    Clicks "Atualizar" and waits for the reloaded table. Never raises.

    Only used by _wait_for_report(), i.e. while the report is NOT ready yet —
    once "Baixar" is on screen the page is never refreshed again (see
    _download_report()). Every step has a short timeout so a stuck page is
    reloaded instead of waiting the context's default 120 s.

    Waits for "domcontentloaded", not "load": the table is all we read, and
    "load" also waits for every image/script/iframe — one hanging resource
    would delay it or keep it from ever firing.
    """
    try:
        await page.get_by_role("link", name=SEL_BTN_UPDATE).click(timeout=STEP_TIMEOUT_MS)
        await page.wait_for_load_state("domcontentloaded", timeout=STEP_TIMEOUT_MS)
        return
    except PlaywrightTimeoutError:
        # The page got stuck mid-refresh (seen right when a report turns
        # "Concluído"). A full reload gets a clean table instead of waiting
        # the context's default 120 s on a page that won't respond.
        log.debug("Refresh stuck, reloading the 156 page...", level="WARN")

    try:
        await page.reload(wait_until="domcontentloaded", timeout=STEP_TIMEOUT_MS * 2)
    except PlaywrightTimeoutError:
        # Swallowed on purpose: the caller's loop reads the table again and
        # refreshes on its next pass, and MAX_REFRESH_ATTEMPTS still bounds
        # the total wait.
        log.debug("Reload of the 156 page timed out, trying again on the next pass.", level="WARN")


async def _wait_until_idle(inflight: Set[Request], sequence: str) -> None:
    """
    Waits (up to STEP_TIMEOUT_MS) until the 156 page has no request in flight,
    plus a short settle delay. Never raises — on timeout the click just goes
    ahead and the busy alert, if any, is handled by the caller.
    """
    waited_ms: int = 0
    while inflight and waited_ms < STEP_TIMEOUT_MS:
        await asyncio.sleep(0.25)
        waited_ms += 250
    if inflight:
        log.debug(f"Sequence {sequence}: page still busy after {STEP_TIMEOUT_MS // 1000} s, clicking anyway.", level="WARN")
    await asyncio.sleep(IDLE_SETTLE_S)


async def _click_and_catch_download(page: Page, link: Locator, sequence: str) -> Download:
    """
    Clicks "Baixar" and returns the first download from ANY page of the
    browser context, not only the 156 page.

    "Baixar" is not a file link: it calls ajaxEnvia('DOW<seq>'), and SSW
    refuses it with the alert MSG_BUSY ("Ja existe uma requisicao em
    andamento.") while another AJAX call of the page is still pending — e.g.
    the queue's own auto-refresh, or our "Atualizar". Playwright dismisses the
    alert, so the click is silently lost. That's why this function tracks the
    page's in-flight requests, only clicks when the page is idle, and re-clicks
    quickly (up to MAX_BUSY_RETRIES) whenever the busy alert still shows up.

    Downloads are listened for on every page of the context, so a file that
    SSW delivers through another page is caught too.

    Raises:
        PlaywrightTimeoutError: When no download starts within DOWNLOAD_TIMEOUT_MS.
    """
    context: BrowserContext = page.context
    loop = asyncio.get_running_loop()
    download_future: asyncio.Future = loop.create_future()
    busy_event: asyncio.Event = asyncio.Event()
    finished_event: asyncio.Event = asyncio.Event()
    inflight: Set[Request] = set()

    def _on_download(download: Download) -> None:
        if not download_future.done():
            download_future.set_result(download)

    def _on_page(new_page: Page) -> None:
        log.debug(f"Sequence {sequence}: 'Baixar' opened a new page: {new_page.url}", level="DBUG")
        new_page.on("download", _on_download)

    def _on_request(request: Request) -> None:
        inflight.add(request)

    def _on_request_done(request: Request) -> None:
        # Fires for requests started before we subscribed too — which is how
        # the pending call behind a busy alert is seen finishing.
        inflight.discard(request)
        finished_event.set()

    async def _on_dialog(dialog: Dialog) -> None:
        if MSG_BUSY in dialog.message:
            busy_event.set()
        else:
            log.debug(f"Sequence {sequence}: dialog ({dialog.type}): {dialog.message}", level="WARN")
        await dialog.accept()

    # Listeners go up BEFORE the click so no event can fire unobserved.
    watched_pages: List[Page] = list(context.pages)
    for watched in watched_pages:
        watched.on("download", _on_download)
        watched.on("dialog", _on_dialog)
    page.on("request", _on_request)
    page.on("requestfinished", _on_request_done)
    page.on("requestfailed", _on_request_done)
    context.on("page", _on_page)

    try:
        await _wait_until_idle(inflight, sequence)
        for busy_count in range(0, MAX_BUSY_RETRIES + 1):
            busy_event.clear()
            finished_event.clear()
            await link.click(timeout=STEP_TIMEOUT_MS)

            # Whichever comes first: the file, or SSW saying it's busy.
            task_busy = asyncio.create_task(busy_event.wait())
            done, _ = await asyncio.wait(
                [download_future, task_busy],
                timeout=DOWNLOAD_TIMEOUT_MS / 1000,
                return_when=asyncio.FIRST_COMPLETED,
            )
            task_busy.cancel()
            await asyncio.gather(task_busy, return_exceptions=True)

            if download_future.done():
                return download_future.result()
            if not done:
                raise PlaywrightTimeoutError(
                    f"Sequence {sequence}: no download within {DOWNLOAD_TIMEOUT_MS // 1000} s."
                )
            if busy_count == MAX_BUSY_RETRIES:
                break
            log.debug(
                f"Sequence {sequence}: SSW busy (\"{MSG_BUSY}\"), "
                f"clicking again when idle ({busy_count + 1}/{MAX_BUSY_RETRIES})...",
                level="WARN",
            )
            # Wait for the pending call to finish (it may have started before
            # we subscribed, so it isn't in `inflight`), then for full idle.
            try:
                await asyncio.wait_for(finished_event.wait(), timeout=STEP_TIMEOUT_MS / 1000)
            except asyncio.TimeoutError:
                log.debug(f"Sequence {sequence}: no pending request seen finishing, retrying anyway.", level="WARN")
            await _wait_until_idle(inflight, sequence)

        raise PlaywrightTimeoutError(
            f"Sequence {sequence}: SSW stayed busy after {MAX_BUSY_RETRIES} re-clicks on 'Baixar'."
        )
    finally:
        context.remove_listener("page", _on_page)
        page.remove_listener("request", _on_request)
        page.remove_listener("requestfinished", _on_request_done)
        page.remove_listener("requestfailed", _on_request_done)
        for watched in watched_pages:
            watched.remove_listener("download", _on_download)
            watched.remove_listener("dialog", _on_dialog)


async def _download_report(
    page: Page,
    sequence: str,
    routine: str,
    domain: str,
    unit_tag: str,
) -> Download:
    """
    Clicks "Baixar" on the sequence row and returns the download, retrying
    the click up to MAX_DOWNLOAD_CLICKS times.

    The file is already "Concluído" at this point, so a click that produces no
    download within DOWNLOAD_TIMEOUT_MS is treated as lost and repeated —
    without clicking "Atualizar" again (see the comment in the loop) —
    instead of failing the whole range.

    Raises:
        PlaywrightTimeoutError: When no click produced a download.
    """
    link: Locator = page.locator(f'tr[seq="{sequence}"]').locator(f"{SEL_COL_ACTION} a")

    for click_count in range(1, MAX_DOWNLOAD_CLICKS + 1):
        log.debug(
            f"Sequence {sequence}: clicking 'Baixar' (click {click_count}/{MAX_DOWNLOAD_CLICKS})...",
            level="INFO",
        )
        try:
            download: Download = await _click_and_catch_download(page, link, sequence)
            log.debug(f"Sequence {sequence}: download started.", level="INFO")
            return download
        except PlaywrightTimeoutError:
            if click_count == MAX_DOWNLOAD_CLICKS:
                raise PlaywrightTimeoutError(
                    f"Sequence {sequence}: no download after {MAX_DOWNLOAD_CLICKS} "
                    f"clicks on 'Baixar' ({DOWNLOAD_TIMEOUT_MS // 1000} s each)."
                )
            log.log(
                level=logging.WARNING,
                domain=domain,
                message=(
                    f"{domain} ROUTINE {routine}{unit_tag} | Sequence {sequence} | "
                    f"No download after {DOWNLOAD_TIMEOUT_MS // 1000} s "
                    f"(click {click_count}/{MAX_DOWNLOAD_CLICKS}), retrying."
                ),
            )
            # No "Atualizar" here on purpose: once "Baixar" is on screen a
            # refresh only fires another AJAX call, which is exactly what makes
            # SSW answer the next click with MSG_BUSY. The link is clicked again
            # as-is, once the page is idle.

    # Unreachable: the loop either returns or raises on the last click.
    raise RuntimeError(f"Sequence {sequence}: download loop exited unexpectedly.")


async def _wait_for_report(
    page: Page,
    sequence: str,
    routine: str,
    domain: str,
    unit: Optional[str] = None,
) -> bool:
    """
    Refreshes the page until the target report row reaches 'Concluído / Baixar'.

    Returns:
        True  — report is ready for download ("Baixar").
        False — report finished but has no data (e.g. "NÃO SELECIONOU CTRCS").
                Caller should skip the download step; page will be closed in finally.

    Raises:
        TimeoutError: When the report does not complete within MAX_REFRESH_ATTEMPTS refreshes.
    """
    row_locator = page.locator(f'tr[seq="{sequence}"]')
    unit_tag = f" Unit: {unit}" if unit else ""

    # Bounded loop so a report that never finishes can't hang the run
    for refresh_count in range(1, MAX_REFRESH_ATTEMPTS + 1):
        situation: str = ""
        status: str = ""
        if await row_locator.count() > 0:
            try:
                situation = await row_locator.locator(SEL_COL_SIT).inner_text(timeout=STEP_TIMEOUT_MS)
                status    = await row_locator.locator(SEL_COL_ACTION).inner_text(timeout=STEP_TIMEOUT_MS)
            except PlaywrightTimeoutError:
                # The row vanished mid-read (page reloading) — try again after the
                # next refresh. Both are cleared so a half read can't be mistaken
                # for "Concluído" with no download link (the no-data case).
                situation, status = "", ""
                log.debug(f"Sequence {sequence}: row read timed out, retrying.", level="WARN")

            if situation.strip() == "Concluído" and status.strip() == "Baixar":
                log.log(
                    level=logging.INFO,
                    domain=domain,
                    message=f"{domain} ROUTINE {routine}{unit_tag} | Sequence {sequence} is ready for download.",
                )
                return True  # success — caller proceeds to download

            if situation.strip() == "Concluído" and status.strip() != "Baixar":
                # Business warning (e.g. "NÃO SELECIONOU CTRCS PARA DEMONSTRATIVO"):
                # report finished but has no data — skip download, do not retry.
                log.log(
                    level=logging.WARNING,
                    domain=domain,
                    message=(
                        f"{domain} ROUTINE {routine}{unit_tag} | "
                        f"Sequence {sequence} completed with status: '{status.strip()}'. Skipping download."
                    ),
                )
                return False  # caller will skip the download step

        log.debug(
            f"Sequence {sequence} not ready [{situation.strip()} / {status.strip()}] "
            f"(refresh {refresh_count}/{MAX_REFRESH_ATTEMPTS}). Refreshing...",
            level="INFO",
        )
        await asyncio.sleep(4)
        await _refresh(page)

    # Loop exhausted — the report never finished within the allowed window
    raise TimeoutError(
        f"{domain} ROUTINE {routine}{unit_tag} | "
        f"Sequence {sequence} did not complete after {MAX_REFRESH_ATTEMPTS} refreshes "
        f"({MAX_REFRESH_ATTEMPTS * 4} s)."
    )


async def process_156_report(
    page: Page,
    date: str,
    routine: str,
    domain: str,
    attempt: int,
    max_attempts: int,
    unit: Optional[str] = None,
) -> None:
    """
    Orchestrates monitoring, downloading, processing, and uploading of a
    report from the 156 screen.

    Raises:
        RuntimeError:  Propagated from _wait_for_report on bad status.
        TimeoutError:  Propagated from _wait_for_report when report never finishes.
        PlaywrightTimeoutError: Propagated from _download_report when no
                       "Baixar" click produced a download.
        ValueError:    When a required parameter (e.g. unit for 076) is missing.
    """
    # The 156 page is closed in the finally below, whether we succeed or raise
    try:
        # 1. Capture sequence number
        await asyncio.sleep(1)

        # nth(0) is the header row; nth(1) is the newly created report
        first_data_row = page.locator(SEL_ROW_DATA).nth(1)
        sequence: str = await first_data_row.get_attribute("seq") or ""

        if not sequence:
            # Fallback: read the first column directly
            sequence = await first_data_row.locator("td:nth-child(1)").inner_text()

        sequence = sequence.strip()

        # Fail immediately if the sequence is still empty after the fallback
        if not sequence:
            raise RuntimeError(
                f"{domain} ROUTINE {routine} | "
                "Could not determine sequence number from the report table."
            )

        unit_tag: str = f" Unit: {unit}" if unit else ""
        log.log(
            level=logging.INFO,
            domain=domain,
            message=f"{domain} ROUTINE {routine}{unit_tag} | Monitoring sequence {sequence} in 156 for routine {routine}.",
        )

        # 2. Monitor table until report is ready
        ready = await _wait_for_report(page, sequence, routine, domain, unit)
        if not ready:
            # No data for this sequence — page will be closed in finally
            return

        # 3. Download the file (a lost "Baixar" click is retried)
        await asyncio.sleep(1)
        download: Download = await _download_report(page, sequence, routine, domain, unit_tag)

        # 4. Persist and process the file
        path_download: Path = cwd / "output" / "downloads" / routine
        path_download.mkdir(parents=True, exist_ok=True)

        # Per-routine naming and post-processing via the dispatch table
        handler = ROUTINE_HANDLERS.get(routine, _handle_default)
        file_name = await handler(download, date, domain, path_download, unit)

        # 5. Upload to SharePoint
        await upload_report_file(
            file_name=file_name,
            routine=routine,
            domain=domain,
            attempt=attempt,
            max_attempts=max_attempts,
        )

        # 6. Column check for routines with a baseline (config/sentinel/<ROUTINE>.yml,
        # or one per header band — see SENTINEL_BASELINES).
        # Runs AFTER processing on purpose — the processor already absorbs
        # known SSW renames (the layout Power BI expects), so the sentinel only
        # flags what is still unknown. It runs after the upload so its line
        # closes this file's block in the domain log; it only reads the local
        # copy (still on disk until main()'s cleanup) and never raises.
        for baseline_name in SENTINEL_BASELINES.get(routine, (routine,)):
            if has_baseline(baseline_name):
                check_file(path_download / file_name, baseline_name, domain)

    finally:
        # Guarantees the page is closed whether we succeed or raise
        await page.close()