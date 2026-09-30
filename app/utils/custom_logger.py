import logging
import sys
import threading

from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Deque

# ---------------------------------------------------------------------------
# Tuning knobs
# ---------------------------------------------------------------------------
MAX_LOG_LINES: int = 1000

# Custom level for the column sentinel's OK line (between INFO and WARNING),
# so a successful column check stands out in the domain log as "SNTL".
# DRIFT/ERROR results keep WARNING/ERROR so they still show up as problems.
SENTINEL: int = 25

# Lock to ensure file read/rewrite is atomic across threads
_file_lock = threading.Lock()

# ---------------------------------------------------------------------------
# 4-character level names — used in BOTH file entries and terminal output
# so the log is always aligned and consistent.
#
# logging.getLevelName() returns full names (WARNING, ERROR, etc.).
# This map overrides them to keep every entry the same width.
# ---------------------------------------------------------------------------
_LEVEL_NAME: dict = {
    logging.DEBUG:    "DBUG",
    logging.INFO:     "INFO",
    SENTINEL:         "SNTL",
    logging.WARNING:  "WARN",
    logging.ERROR:    "ERRO",
    logging.CRITICAL: "CRIT",
}

# ---------------------------------------------------------------------------
# ANSI color map — keys match _LEVEL_NAME values
# ---------------------------------------------------------------------------
_ANSI: dict = {
    "DBUG": "\033[37m",   # White/Grey
    "INFO": "\033[36m",   # Cyan
    "SNTL": "\033[32m",   # Green
    "WARN": "\033[33m",   # Yellow
    "ERRO": "\033[31m",   # Red
    "CRIT": "\033[35m",   # Magenta
    "RSET": "\033[0m",    # Reset
}


def _single_line(message: str) -> str:
    """
    Collapses a message to one line before it reaches the file.

    Playwright errors carry a multi-line "=== logs ===" / "Call log:" block;
    written as-is it breaks the one-entry-per-line file (and reads upside down,
    since entries are prepended). Only the text before that block is kept.
    """
    for marker in ("\n=", "\nCall log:"):
        message = message.split(marker, 1)[0]
    return " ".join(message.split())


class CustomLogger:
    def __init__(self) -> None:
        self.log_dir = Path.cwd() / "output" / "logs"
        self.log_dir.mkdir(parents=True, exist_ok=True)

        # 24-hour format — unambiguous and locale-independent
        self._file_fmt     = "%d/%m/%Y %H:%M:%S"
        self._terminal_fmt = "%H:%M:%S"

    def _get_log_file_path(self, domain: str) -> Path:
        return self.log_dir / f"{domain}.log"

    # ------------------------------------------------------------------
    # FILE logger
    # ------------------------------------------------------------------
    def log(self, level: int, domain: str, message: str) -> None:
        """
        Prepends a log entry to the domain log file.
        The file never exceeds MAX_LOG_LINES lines.

        Level names are 4 characters wide (DBUG / INFO / WARN / ERRO / CRIT)
        so every line in the log file is consistently aligned.

        :param level:   logging constant (logging.INFO, logging.ERROR, …)
                        or SENTINEL for the column sentinel's OK line
        :param domain:  determines which .log file receives the entry
        :param message: text to record
        """
        file_log   = self._get_log_file_path(domain)
        # Use _LEVEL_NAME instead of logging.getLevelName() so the file
        # entries use the same 4-char abbreviations as the terminal.
        level_name = _LEVEL_NAME.get(level, logging.getLevelName(level)[:4].upper())
        timestamp  = datetime.now().strftime(self._file_fmt)
        log_entry  = f"{timestamp} - {level_name} - {_single_line(message)}"

        with _file_lock:
            try:
                raw: str = file_log.read_text(encoding="utf-8")
                existing: Deque[str] = deque(
                    raw.splitlines(keepends=True),
                    maxlen=MAX_LOG_LINES - 1,
                )
            except FileNotFoundError:
                existing = deque(maxlen=MAX_LOG_LINES - 1)

            with file_log.open("w", encoding="utf-8") as f:
                f.write(log_entry + "\n")
                f.writelines(existing)

    # ------------------------------------------------------------------
    # TERMINAL printer
    # ------------------------------------------------------------------
    def debug(self, message: str, level: str = "INFO") -> None:
        """
        Prints a color-coded, timestamped message to the terminal.
        Does NOT write to any log file — terminal only.

        :param message: text to display
        :param level:   one of DBUG | INFO | SNTL | WARN | ERRO | CRIT
                        defaults to INFO

        Usage:
            log = get_logger()
            log.debug("Starting routine 103")
            log.debug("Something went wrong", level="ERRO")
        """
        level     = level.upper()
        color     = _ANSI.get(level, _ANSI["DBUG"])
        reset     = _ANSI["RSET"]
        timestamp = datetime.now().strftime(self._terminal_fmt)
        stream    = sys.stderr if level == "ERRO" else sys.stdout

        print(f"{color}[{timestamp} {level}]{reset} {message}", file=stream)

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------
    def clear_log(self, domain: str) -> None:
        """
        Clears the log file for a given domain.

        The wipe happens BEFORE the "cleared" notice is logged. Doing it the
        other way around would erase the very entry meant to leave a trace of
        the wipe, leaving the log at 0 bytes with no history. Wiping first
        guarantees the notice survives as line 1 of the fresh file.
        """
        file_log = self._get_log_file_path(domain)
        with _file_lock:
            file_log.write_text("", encoding="utf-8")

        self.log(
            level=logging.WARNING,
            domain=domain,
            message=f"Log file for domain '{domain}' was cleared.",
        )


# ---------------------------------------------------------------------------
# Singleton — thread-safe double-checked locking
# ---------------------------------------------------------------------------
_logger_instance: CustomLogger | None = None
_logger_lock = threading.Lock()


def get_logger() -> CustomLogger:
    """
    Returns the singleton CustomLogger instance.

    Usage in any module:
        from app.utils.custom_logger import get_logger
        log = get_logger()

        log.log(level=logging.INFO, domain="AGE", message="Done.")  # -> file
        log.debug("Starting...")                                      # -> terminal
        log.debug("Upload failed", level="ERRO")                     # -> terminal (red)
    """
    global _logger_instance
    if _logger_instance is None:
        with _logger_lock:
            if _logger_instance is None:
                _logger_instance = CustomLogger()
    return _logger_instance
