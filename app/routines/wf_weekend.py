import logging

from datetime import datetime

from config.settings.variables import settings
from app.utils.custom_logger import get_logger
from . import bot_455ABH, bot_455CGK
# from . import bot_455F  # enable only in domains that requested it

log = get_logger()

async def _run_sub_routine(func, name):
    """Helper function to execute a subroutine and catch errors."""
    try:
        await func()
    except Exception as e:
        log.log(logging.ERROR, settings.ssw_domain, f"--- [WF-455W] FALHA na sub-rotina: {name}: {e} ---")

async def main():
    """
    Main function of WORKFLOW 455 Weekend.
    It decides which routine to run based on the day of the week: Saturday
    runs bot_455ABH.py, Sunday runs bot_455CGK.py. The 12-month backfill is
    decided inside each bot (_build_date_ranges: its weekday + BACKFILL_HOUR,
    15h by default), so the cron time of WF_WE must match that hour — at any
    other hour the bots fall back to previous month + current month.
    bot_455F.py is disabled by default (see the commented-out call below) —
    enable only in domains that requested it.
    """
    # weekday() -> Monday=0, Sunday=6
    weekday = datetime.now().weekday()

    if weekday == 5: # Saturday
        log.log(logging.INFO, settings.ssw_domain, "[WF-455W] Executando bloco de Sábado.")
        await _run_sub_routine(bot_455ABH.main, "455ABH")
        # enable only in domains that requested it
        # await _run_sub_routine(bot_455F.main, "455F")
    elif weekday == 6: # Sunday
        log.log(logging.INFO, settings.ssw_domain, "[WF-455W] Executando bloco de Domingo.")
        await _run_sub_routine(bot_455CGK.main, "455CGK")
    else:
        log.log(logging.INFO, settings.ssw_domain, "[WF-455W] Erro na execução. Finalizando.")
