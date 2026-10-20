import argparse
import asyncio
import importlib
import sys

from datetime import datetime
from typing import Dict, List

from config.settings.variables import settings

# ---------------------------------------------------------------------------
# Terminal debug printer
# Kept intentionally separate from CustomLogger — terminal output gives
# real-time visibility during a run without touching log files. The `level`
# is color-coded via ANSI codes so ERROR/WARNING/INFO are visually distinct.
# ---------------------------------------------------------------------------

ANSI = {
    "INFO":    "\033[36m",   # Cyan
    "WARNING": "\033[33m",   # Yellow
    "ERROR":   "\033[31m",   # Red
    "DEBUG":   "\033[37m",   # White/Grey
    "RESET":   "\033[0m",
}

def log_debug(message: str, level: str = "INFO") -> None:
    """
    Prints a timestamped message to the terminal.

    :param message: Message to display.
    :param level:   One of INFO | WARNING | ERROR | DEBUG. Defaults to INFO.
    """
    timestamp = datetime.now().strftime("%H:%M:%S")
    color     = ANSI.get(level.upper(), ANSI["DEBUG"])
    reset     = ANSI["RESET"]
    stream    = sys.stderr if level.upper() == "ERROR" else sys.stdout
    print(f"{color}[{timestamp} {level.upper()}]{reset} {message}", file=stream)


# ---------------------------------------------------------------------------
# Routine map — strings only, modules imported dynamically at call time
# ---------------------------------------------------------------------------
ROUTINE_MAP: Dict[str, str] = {
    # --- Individual bots (manual runs / tests) ---
    "458":      "app.routines.bot_458.main",
    "930":      "app.routines.bot_930.main",
    "916":      "app.routines.bot_916.main",
    "571":      "app.routines.bot_571.main",
    "493":      "app.routines.bot_493.main",
    "477":      "app.routines.bot_477.main",
    "467":      "app.routines.bot_467.main",
    "455CGK":   "app.routines.bot_455CGK.main",
    "455ABH":   "app.routines.bot_455ABH.main",
    "455F":     "app.routines.bot_455F.main",
    "452":      "app.routines.bot_452.main",
    "441":      "app.routines.bot_441.main",
    "442":      "app.routines.bot_442.main",
    "435":      "app.routines.bot_435.main",
    "411":      "app.routines.bot_411.main",
    "324":      "app.routines.bot_324.main",
    "322":      "app.routines.bot_322.main",
    "306":      "app.routines.bot_306.main",
    "200":      "app.routines.bot_200.main",
    "103":      "app.routines.bot_103.main",
    "119":      "app.routines.bot_119.main",
    "081":      "app.routines.bot_081.main",
    "076":      "app.routines.bot_076.main",
    "073":      "app.routines.bot_073.main",
    "056":      "app.routines.bot_056.main",
    "019":      "app.routines.bot_019.main",
    "036":      "app.routines.bot_036.main",
    "002":      "app.routines.bot_002.main",
    # --- Workflows (called by scheduler.sh) ---
    "WF_ALL":   "app.routines.wf_startall.main",
    "WF_DAILY": "app.routines.wf_daily.main",
    "WF_WE":    "app.routines.wf_weekend.main",
}


def _validate_routine_map() -> None:
    """
    Validates that every value in ROUTINE_MAP contains a dot separating
    the module path from the function name. Catches typos at startup.
    """
    bad = [key for key, path in ROUTINE_MAP.items() if "." not in path]
    if bad:
        raise ValueError(f"ROUTINE_MAP has invalid entries (missing '.'): {bad}")


def _load_routine(routine_id: str):
    """
    Dynamically imports and returns the coroutine function for a given
    routine ID.

    Raises:
        ImportError:    Module not found.
        AttributeError: Function name not found inside the module.
    """
    target_path            = ROUTINE_MAP[routine_id]
    module_path, func_name = target_path.rsplit(".", 1)

    log_debug(f"Importing: {module_path}.{func_name}")
    module = importlib.import_module(module_path)
    log_debug(f"Import of '{module_path}' OK.")
    return getattr(module, func_name)


async def run_orchestrator(routine_ids: List[str]) -> None:
    """
    Main orchestrator. Runs one or more routines/workflows sequentially.
    Each routine failure is isolated — the batch continues with the next one.
    """
    total   = len(routine_ids)
    failed  : List[str] = []
    skipped : List[str] = []

    log_debug(f"Batch started: {total} routine(s) → {routine_ids}")

    for routine_id in routine_ids:

        if routine_id not in ROUTINE_MAP:
            log_debug(f"Unknown routine '{routine_id}'. Skipping.", level="WARNING")
            skipped.append(routine_id)
            continue

        log_debug(f"===> Starting '{routine_id}' <===")

        try:
            routine_fn = _load_routine(routine_id)
            await routine_fn()
            log_debug(f"===> '{routine_id}' completed successfully <===")

        except ImportError as e:
            log_debug(
                f"Import error for '{routine_id}': {e}. "
                f"Make sure '{ROUTINE_MAP[routine_id]}' exists and has no asyncio.run() at module level.",
                level="ERROR",
            )
            failed.append(routine_id)

        except Exception as e:
            log_debug(f"'{routine_id}' failed: {e}", level="ERROR")
            failed.append(routine_id)

    # Structured summary printed at the end of every batch
    succeeded     = total - len(failed) - len(skipped)
    summary_level = "WARNING" if failed else "INFO"
    log_debug(
        f"Batch finished — "
        f"Success: {succeeded} | Failed: {len(failed)} | Skipped: {len(skipped)}"
        + (f" | Failed routines: {failed}"   if failed  else "")
        + (f" | Skipped routines: {skipped}" if skipped else ""),
        level=summary_level,
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    _validate_routine_map()

    log_debug(f"main.py started. ROUTINE_MAP has {len(ROUTINE_MAP)} targets.")

    parser = argparse.ArgumentParser(description=f"{settings.ssw_domain} Routine Orchestrator.")
    parser.add_argument(
        "routines",
        nargs="+",
        type=str,
        help="One or more routine/workflow IDs to run (e.g. 455CGK 930 WF_ALL).",
    )
    args = parser.parse_args()
    log_debug(f"Arguments received: {args.routines}")

    try:
        asyncio.run(run_orchestrator(routine_ids=args.routines))
        log_debug(f"Execution of {args.routines} finished.", level="INFO")
        sys.exit(0)

    # KeyboardInterrupt is handled separately — not an application error
    except KeyboardInterrupt:
        log_debug("Interrupted by user (Ctrl+C).", level="WARNING")
        sys.exit(130)  # standard UNIX exit code for SIGINT

    except Exception as e:
        log_debug(f"Catastrophic failure during {args.routines}: {e}", level="ERROR")
        sys.exit(1)
        