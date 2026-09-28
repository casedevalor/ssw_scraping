import logging
from pathlib import Path
from typing import Union

import send2trash

from app.utils.custom_logger import get_logger

log = get_logger()

class GarbageCollector:
    """Class responsible for cleaning specified directories by moving files to the trash."""

    def delete(self, *paths: Union[str, Path]) -> None:
        """
        Deletes all files within the specified directories by moving them to the trash.
        
        :param paths: Directories from which files should be removed.
        """
        for path in paths:
            try:
                p = Path(path)
                if not p.exists() or not p.is_dir():
                    continue

                # Iterates over all files in the directory, sending them to the trash
                for file_path in p.rglob("*"):
                    if file_path.is_file():
                        send2trash.send2trash(str(file_path))

            except Exception as e:
                log.log(level=logging.ERROR, domain="SYSTEM", message=f"Error deleting files in {path}: {str(e)}")