import asyncio
import logging

from pathlib import Path
from typing import Optional

from playwright.async_api import Page, Download

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

# ---------------------------------------------------------------------------
# Tuning knobs
# ---------------------------------------------------------------------------
MAX_REFRESH_ATTEMPTS = 120  # 120 × 4 s = 8 min max wait — some companies take longer to release the file
DOWNLOAD_TIMEOUT_MS  = 120_000  # 2 min — bounded, never wait forever for a download


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

    # Default: covers 455CGK, 455ABH, 455F, 493, 467 and any future routine
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
    "930":    _handle_930,
}


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
        if await row_locator.count() > 0:
            situation = await row_locator.locator(SEL_COL_SIT).inner_text()
            status    = await row_locator.locator(SEL_COL_ACTION).inner_text()

            if situation.strip() == "Concluído" and status.strip() == "Baixar":
                log.log(
                    level=logging.INFO,
                    domain=domain,
                    message=f"Sequence {sequence} is ready for download.",
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
            f"Sequence {sequence} not ready "
            f"(refresh {refresh_count}/{MAX_REFRESH_ATTEMPTS}). Refreshing...",
            level="INFO",
        )
        await asyncio.sleep(4)
        await page.get_by_role("link", name=SEL_BTN_UPDATE).click()

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

        log.log(
            level=logging.INFO,
            domain=domain,
            message=f"Monitoring sequence {sequence} for routine {routine}...",
        )

        # 2. Monitor table until report is ready
        ready = await _wait_for_report(page, sequence, routine, domain, unit)
        if not ready:
            # No data for this sequence — page will be closed in finally
            return

        # 3. Download the file
        await asyncio.sleep(1)
        row_locator = page.locator(f'tr[seq="{sequence}"]')

        # The context manager must be active BEFORE the click that triggers the
        # download, otherwise the event can fire before Playwright starts listening.
        async with page.expect_download(timeout=DOWNLOAD_TIMEOUT_MS) as download_info:
            await row_locator.locator(f"{SEL_COL_ACTION} a").click()

        download = await download_info.value

        # 4. Persist and process the file
        path_download: Path = cwd / "output" / "downloads" / routine
        path_download.mkdir(parents=True, exist_ok=True)

        # Per-routine naming and post-processing via the dispatch table
        handler = ROUTINE_HANDLERS.get(routine, _handle_default)
        file_name = await handler(download, date, domain, path_download, unit)

        # Column check for routines with a baseline (config/sentinel/<ROUTINE>.yml).
        # Runs AFTER processing on purpose — the processor already absorbs
        # known SSW renames (the layout Power BI expects), so the sentinel only
        # flags what is still unknown. It adds one line to the domain log and
        # never raises, so the upload below always proceeds.
        if has_baseline(routine):
            check_file(path_download / file_name, routine, domain)

        # 5. Upload to SharePoint
        await upload_report_file(
            file_name=file_name,
            routine=routine,
            domain=domain,
            attempt=attempt,
            max_attempts=max_attempts,
        )

        log.log(
            level=logging.INFO,
            domain=domain,
            message=(
                f"{domain} ROUTINE {routine} | "
                f"Sequence {sequence} — download and upload successful."
            ),
        )

    finally:
        # Guarantees the page is closed whether we succeed or raise
        await page.close()