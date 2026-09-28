import asyncio
import importlib
import logging

from typing import List, Tuple

from app.utils.custom_logger import get_logger
from config.settings.variables import settings

log    = get_logger()
DOMAIN = settings.ssw_domain
WF_TAG = "WF_ALL"

# ---------------------------------------------------------------------------
# Routines to execute — strings only, dynamically imported at runtime.
#
# Same dynamic-import strategy as main.py: a broken bot module fails only
# its own entry instead of preventing the whole workflow from starting.
# ---------------------------------------------------------------------------
ROUTINES: List[Tuple[str, str]] = [
    ("930",    "app.routines.bot_930.main"),
    ("571",    "app.routines.bot_571.main"),
    ("493",    "app.routines.bot_493.main"),
    ("467",    "app.routines.bot_467.main"),
    ("455ABH", "app.routines.bot_455ABH.main"),
    ("455CGK", "app.routines.bot_455CGK.main"),
    ("435",    "app.routines.bot_435.main"),
    ("411",    "app.routines.bot_411.main"),
    ("200",    "app.routines.bot_200.main"),
    ("306",    "app.routines.bot_306.main"),
    ("103",    "app.routines.bot_103.main"),
    ("036",    "app.routines.bot_036.main"),
    ("002",    "app.routines.bot_002.main"),
    ("441",    "app.routines.bot_441.main"),
    ("477",    "app.routines.bot_477.main"),
    ("081",    "app.routines.bot_081.main"),
]


async def _run_sub_routine(name: str, module_path: str) -> bool:
    """
    Dynamically imports and executes a single bot routine.

    Returns True/False instead of raising, so the caller can track
    success/failure per routine without one failure blocking the batch.

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
    Main function of WORKFLOW START ALL.
    Runs all routines sequentially, isolating failures so one broken
    routine does not stop the rest.

    Logs a structured success/failure summary at the end.
    """
    total   = len(ROUTINES)
    failed  : List[str] = []

    log.log(
        level=logging.INFO,
        domain=DOMAIN,
        message=f"{WF_TAG} | Starting batch of {total} routine(s).",
    )

    for name, module_path in ROUTINES:
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

    # Structured summary at the end of the workflow
    succeeded     = total - len(failed)
    summary_level = logging.WARNING if failed else logging.INFO
    log.log(
        level=summary_level,
        domain=DOMAIN,
        message=(
            f"{WF_TAG} | Batch finished — "
            f"Success: {succeeded} | Failed: {len(failed)}"
            + (f" | Failed routines: {failed}" if failed else "")
        ),
    )