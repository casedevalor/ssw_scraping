import asyncio
import importlib
import logging

from datetime import datetime
from typing import List, Tuple

from config.settings.variables import settings
from app.utils.custom_logger import get_logger

log    = get_logger()
DOMAIN = settings.ssw_domain
WF_TAG = "WF_DAILY"

# ---------------------------------------------------------------------------
# Routine registry — strings only, dynamically imported at runtime
# (same dynamic-import approach as wf_startall.py — routines are never
# imported eagerly)
# ---------------------------------------------------------------------------
BLOCK_1H: List[Tuple[str, str]] = [
    ("930",    "app.routines.bot_930.main"),
    ("455ABH", "app.routines.bot_455ABH.main"),
    ("455CGK", "app.routines.bot_455CGK.main"),
]

BLOCK_12H: List[Tuple[str, str]] = [
    ("930",    "app.routines.bot_930.main"),
    ("455ABH", "app.routines.bot_455ABH.main"),
    ("455CGK", "app.routines.bot_455CGK.main"),
    ("103",    "app.routines.bot_103.main"),
    ("036",    "app.routines.bot_036.main"),
]

BLOCK_DEFAULT: List[Tuple[str, str]] = [
    ("930",    "app.routines.bot_930.main"),
    ("103",    "app.routines.bot_103.main"),
    ("036",    "app.routines.bot_036.main"),
]


async def _run_sub_routine(name: str, module_path: str) -> bool:
    """
    Dynamically imports and executes a single bot routine.

    Returns True/False instead of raising, so the caller can track
    success/failure per routine without one failure blocking the block.

    :param name:        Human-readable routine ID (e.g. "930").
    :param module_path: Dotted import path to the main() coroutine.
    :return:            True on success, False on any failure.
    """
    try:
        mod_path, func_name = module_path.rsplit(".", 1)
        module     = importlib.import_module(mod_path)
        routine_fn = getattr(module, func_name)
        await routine_fn()
        return True

    except ImportError as e:
        log.log(
            level=logging.ERROR,
            domain=DOMAIN,
            message=(
                f"{WF_TAG} | Import error for '{name}': {e}. "
                f"Make sure '{module_path}' exists and has no asyncio.run() at module level."
            ),
        )
        return False

    except Exception as e:
        log.log(
            level=logging.ERROR,
            domain=DOMAIN,
            message=f"{WF_TAG} | Failure in sub-routine '{name}': {e}",
        )
        return False


async def main() -> None:
    """
    Main function of WORKFLOW DAILY.
    Decides which block of routines to run based on the current hour.

    Blocks:
        01h → 930, 455ABH, 455CGK          (clears the log on weekdays)
        12h → 930, 455ABH, 455CGK, 103, 036
        *   → 930, 103, 036
    """
    # Evaluated at call time, not import time, so the block matches the real run hour.
    now     = datetime.now()
    hour    = now.hour
    weekday = now.weekday()  # 0=Monday … 6=Sunday

    # Clear the log at 1h on weekdays (Mon–Fri)
    if hour == 1 and 0 <= weekday <= 4:
        log.clear_log(domain=DOMAIN)

    if hour == 1:
        block_name      = "01h"
        routines_to_run = BLOCK_1H
    elif hour == 12:
        block_name      = "12h"
        routines_to_run = BLOCK_12H
    else:
        block_name      = f"{hour:02d}h"
        routines_to_run = BLOCK_DEFAULT

    total  = len(routines_to_run)
    failed : List[str] = []

    log.log(
        level=logging.INFO,
        domain=DOMAIN,
        message=f"{WF_TAG} | Starting block {block_name} — {total} routine(s).",
    )

    for name, module_path in routines_to_run:
        log.log(
            level=logging.INFO,
            domain=DOMAIN,
            message=f"{WF_TAG} | ===> Starting '{name}' <===",
        )

        success = await _run_sub_routine(name, module_path)

        if not success:
            failed.append(name)
        else:
            log.log(
                level=logging.INFO,
                domain=DOMAIN,
                message=f"{WF_TAG} | ===> '{name}' completed successfully <===",
            )

    # Structured summary at the end of the block
    succeeded     = total - len(failed)
    summary_level = logging.WARNING if failed else logging.INFO
    log.log(
        level=summary_level,
        domain=DOMAIN,
        message=(
            f"{WF_TAG} | Block {block_name} finished — "
            f"Success: {succeeded} | Failed: {len(failed)}"
            + (f" | Failed routines: {failed}" if failed else "")
        ),
    )