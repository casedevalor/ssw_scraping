import asyncio
import logging

from playwright.async_api import Page

from config.settings.variables import settings
from app.utils.custom_logger import get_logger

log = get_logger()

# ---------------------------------------------------------------------------
# Selectors
# ---------------------------------------------------------------------------
SEL_FIELD_DOMAIN   = "[id=\"\\31 \"]"
SEL_FIELD_CPF      = "[id=\"\\32 \"]"
SEL_FIELD_USER     = "[id=\"\\33 \"]"
SEL_FIELD_PASSWORD = "[id=\"\\34 \"]"
SEL_BTN_SUBMIT     = "►"
SEL_ERROR_LABEL    = "#errormsglabel"
SEL_BTN_OK         = "7. OK"

# ---------------------------------------------------------------------------
# Login URL — overridable via settings.ssw_url
# ---------------------------------------------------------------------------
LOGIN_URL: str = getattr(settings, "ssw_url", "https://sistema2.ssw.inf.br")

# ---------------------------------------------------------------------------
# Bounded retry — won't loop forever if credentials are wrong
# ---------------------------------------------------------------------------
MAX_LOGIN_ATTEMPTS: int = 5

async def check_login_error(page: Page, routine: str) -> bool:
    """
    Checks whether a login error message is visible on the page.
    If so, dismisses the modal and logs the error.
    
    Exceptions are re-raised, not swallowed, so the caller gets the real failure.

    :param page:    The page where login was performed.
    :param routine: Routine name included in the log message.
    :return:        True if a login error was found, False otherwise.
    :raises:        Re-raises any unexpected Playwright exception.
    """
    error_locator = page.locator(SEL_ERROR_LABEL)

    if await error_locator.is_visible():
        text: str = await error_locator.inner_text()
        if text:
            log.log(
                level=logging.WARNING,
                domain=settings.ssw_domain,
                message=f"{settings.ssw_domain} ROUTINE {routine} | Login error: {text}",
            )
            await page.get_by_role("link", name=SEL_BTN_OK).click()
            return True

    return False

async def perform_login(page: Page, routine: str) -> None:
    """
    Performs login to the system using stored credentials.
    Retries up to MAX_LOGIN_ATTEMPTS times on failure.

    Bounded retry loop — raises RuntimeError when attempts are exhausted.
    
    :param page:    The page where login will be performed.
    :param routine: Routine name included in log messages.
    :raises RuntimeError: When all login attempts fail.
    """
    for attempt in range(1, MAX_LOGIN_ATTEMPTS + 1):
        try:
            await page.goto(LOGIN_URL, wait_until="load")

            await page.wait_for_timeout(250)
            await page.locator(SEL_FIELD_DOMAIN).fill(settings.ssw_domain)
            await page.wait_for_timeout(250)
            await page.locator(SEL_FIELD_CPF).fill(settings.ssw_cpf)
            await page.wait_for_timeout(250)
            await page.locator(SEL_FIELD_USER).fill(settings.ssw_user)
            await page.wait_for_timeout(250)
            await page.locator(SEL_FIELD_PASSWORD).fill(settings.ssw_password)

            await page.get_by_role("link", name=SEL_BTN_SUBMIT).click()
            await page.wait_for_timeout(1000)
            
            if await check_login_error(page, routine):
                log.log(
                    level=logging.WARNING,
                    domain=settings.ssw_domain,
                    message=(
                        f"{settings.ssw_domain} ROUTINE {routine} | "
                        f"Login failed (attempt {attempt}/{MAX_LOGIN_ATTEMPTS}). Retrying..."
                    ),
                )
                continue

            log.log(
                level=logging.INFO,
                domain=settings.ssw_domain,
                message=f"{settings.ssw_domain} ROUTINE {routine} | Login successful.",
            )
            return

        except Exception as e:
            log.log(
                level=logging.ERROR,
                domain=settings.ssw_domain,
                message=(
                    f"{settings.ssw_domain} ROUTINE {routine} | "
                    f"Unexpected error during login (attempt {attempt}/{MAX_LOGIN_ATTEMPTS}): {e}"
                ),
            )
            if attempt < MAX_LOGIN_ATTEMPTS:
                await asyncio.sleep(5)

    raise RuntimeError(
        f"{settings.ssw_domain} ROUTINE {routine} | "
        f"Login failed after {MAX_LOGIN_ATTEMPTS} attempts. Aborting."
    )
