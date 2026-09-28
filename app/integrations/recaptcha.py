from typing import Optional
from anticaptchaofficial.recaptchav2proxyless import recaptchaV2Proxyless
from config.settings.variables import settings


class Recaptcha:
    def solve_recaptcha(self, data_sitekey: str) -> Optional[str]:
        """
        Solves reCAPTCHA V2 using the AntiCaptcha service.
        
        :param data_sitekey: Site-specific key for reCAPTCHA.
        :return: reCAPTCHA response if successful, or None if it fails.
        """
        # Initialize the reCAPTCHA solver
        solver = recaptchaV2Proxyless()
        solver.set_verbose(1)
        solver.set_key(settings.key_api_recaptcha)
        solver.set_website_url(settings.yaml_config.anticaptcha.links.link_recaptcha_ssw)
        solver.set_website_key(data_sitekey)

        # Attempt to solve the reCAPTCHA
        response = solver.solve_and_return_solution()

        # Check and return response if successful
        if response != 0:
            return response
        else:
            # Print error if solution fails
            print(f"Error solving reCAPTCHA: {solver.err_string}")
            return None
