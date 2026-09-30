import logging

from pathlib import Path
from typing import Optional

from config.settings.variables import settings
from app.utils.custom_logger import get_logger
from app.integrations.sharepoint import get_sharepoint_client

log = get_logger()

cwd = Path.cwd()


def _tenant() -> str:
    """
    Tenant prefix of the SharePoint host (e.g. "softweb268" from
    "softweb268.sharepoint.com"), shown in front of every remote folder in the
    log so whoever reads it knows which SharePoint the file went to — each
    company copy may point at a different tenant.
    """
    return settings.sharepoint_domain_name.split(".")[0]


def _resolve_remote_folder(routine: str, domain: str, report: Optional[str]) -> str:
    """
    Business rule for the remote folder path, kept in one place so routine-specific
    overrides are easy to find and extend.

    Rules:
        - 455ABH maps to the shared '455' folder
        - 056 requires a sub-folder matching the report number
        - All others use the routine ID directly
    """
    if routine == "455ABH":
        folder = "455"
    elif routine == "056" and report:
        folder = f"056/{report}"
    else:
        folder = routine

    return f"Banco_de_Dados/{domain}/{folder}"


async def upload_report_file(
    file_name: str,
    routine: str,
    domain: str,
    attempt: int,
    max_attempts: int,
    report: Optional[str] = None,
) -> None:
    """
    Uploads a report file to the correct SharePoint folder.

    Raises:
        FileNotFoundError: When the local file does not exist.
        Exception:         Re-raised from the SharePoint client on upload failure.
    """
    local_path = cwd / "output" / "downloads" / routine / file_name

    if not local_path.is_file():
        log.log(
            level=logging.ERROR,
            domain=domain,
            message=(
                f"{domain} ROUTINE {routine} | "
                f"Local file not found: {local_path} "
                f"- attempt {attempt}/{max_attempts}."
            ),
        )
        raise FileNotFoundError(f"Local file not found: {local_path}")

    # Resolved before the try so the except clause can safely reference it
    base_remote_folder = _resolve_remote_folder(routine, domain, report)

    try:
        client = get_sharepoint_client()

        web_url = client.upload_file(
            local_path=str(local_path),
            remote_folder=base_remote_folder,
            file_name=file_name,
        )

        log.log(
            level=logging.INFO,
            domain=domain,
            message=(
                f"{domain} ROUTINE {routine} | "
                f"'{file_name}' uploaded to '{_tenant()}/{base_remote_folder}' "
                f"- attempt {attempt}/{max_attempts}."
            ),
        )

    except Exception as e:
        log.log(
            level=logging.ERROR,
            domain=domain,
            message=(
                f"{domain} ROUTINE {routine} | "
                f"Upload failed for '{file_name}' to '{_tenant()}/{base_remote_folder}': {e} "
                f"- attempt {attempt}/{max_attempts}."
            ),
        )
        raise


def upload_log_file(domain: str, routine: Optional[str] = None) -> None:
    """
    Uploads the domain log file to the BOTLOGS folder in SharePoint.

    `routine` only tags the log lines written here (`<DOMAIN> ROUTINE <routine> | ...`),
    so they follow the same pattern as the rest of the run.

    If the log file does not exist, logs a warning and returns without raising.
    Upload failures are logged and re-raised so the caller is aware.
    """
    local_path  = cwd / "output" / "logs" / f"{domain}.log"
    remote_folder = "Banco_de_Dados/BOTLOGS"
    prefix: str = f"{domain} ROUTINE {routine} | " if routine else f"{domain} | "

    if not local_path.is_file():
        log.log(
            level=logging.WARNING,
            domain=domain,
            message=f"{prefix}Log file '{domain}.log' not found. Skipping upload.",
        )
        return

    try:
        client = get_sharepoint_client()

        web_url = client.upload_file(
            local_path=str(local_path),
            remote_folder=remote_folder,
            file_name=f"{domain}.log",
        )

        log.log(
            level=logging.INFO,
            domain=domain,
            message=f"{prefix}Log file '{domain}.log' uploaded to '{_tenant()}/{remote_folder}'.",
        )

    except Exception as e:
        # Persist the failure to the file log, consistent with the rest of the project
        log.log(
            level=logging.ERROR,
            domain=domain,
            message=f"{prefix}Failed to upload log file '{domain}.log' to '{_tenant()}/{remote_folder}': {e}",
        )
        raise