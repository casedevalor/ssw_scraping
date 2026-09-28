import os
import requests
from msal import ConfidentialClientApplication
from config.settings.variables import settings
from app.utils.custom_logger import get_logger


log = get_logger()

class SharePointApi:
    def __init__(self):
        """
        Initializes authentication and obtains the access token for Graph API.
        """
        authority = f"https://login.microsoftonline.com/{settings.tenant_id}"
        self.app = ConfidentialClientApplication(
            settings.client_id,
            authority=authority,
            client_credential=settings.client_secret
        )
        self.access_token = self._get_token()
        self.headers = {
            "Authorization": f"Bearer {self.access_token}"
        }

        self.site_id = self._get_site_id()
        self.drive_id = self._get_drive_id()

    def _get_token(self) -> str:
        result = self.app.acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])
        token = result.get("access_token")
        if not token:
            raise RuntimeError(f"Falha ao obter token: {result}")
        return token

    def _get_site_id(self) -> str:
        """
        Gets the SharePoint site_id based on the domain and site name.
        """
        url = f"https://graph.microsoft.com/v1.0/sites/{settings.sharepoint_domain_name}:/sites/{settings.sharepoint_site_name}"
        response = requests.get(url, headers=self.headers)
        if response.status_code != 200:
            raise RuntimeError(f"Erro ao obter site_id: {response.status_code} - {response.text}")
        return response.json()["id"]

    def _get_drive_id(self) -> str:
        url = f"https://graph.microsoft.com/v1.0/sites/{self.site_id}/drives"
        response = requests.get(url, headers=self.headers)
        if response.status_code != 200:
            raise RuntimeError(f"Erro ao obter drive_id: {response.status_code} - {response.text}")

        # print("== LIST OF DRIVES FOUND ON THE SITE ==")
        # for d in response.json()["value"]:
        #     print(f"- {d['name']}")

        drives = [d for d in response.json()["value"] if d["name"] == settings.sharepoint_library]
        if not drives:
            raise RuntimeError("Drive 'Documents' não encontrado.")
        return drives[0]["id"]

    def upload_file(self, local_path: str, remote_folder: str, file_name: str) -> str:
        """
        Uploads a file to the SharePoint document library.
        
        :param local_path: local path of the file
        :param remote_folder: folder path in SharePoint (ex: "Banco_de_Dados/NRE")
        :param file_name: final name of the file
        :return: Web URL of the uploaded file
        """
        if not os.path.isfile(local_path):
            raise FileNotFoundError(f"Arquivo '{local_path}' não encontrado.")

        remote_path = f"{remote_folder}/{file_name}".strip("/")
        upload_url = f"https://graph.microsoft.com/v1.0/drives/{self.drive_id}/root:/{remote_path}:/content"

        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/octet-stream"
        }

        with open(local_path, "rb") as f:
            resp = requests.put(upload_url, headers=headers, data=f)

        if resp.status_code in (200, 201):
            # Tenant prefix (e.g. "softweb827") so multi-tenant setups can tell
            # which SharePoint the file landed on at a glance in the terminal log.
            tenant = settings.sharepoint_domain_name.split(".")[0]
            log.debug(f"Arquivo '{file_name}' enviado para '{tenant}/{remote_folder}'.,", level="INFO")
            return resp.json().get("webUrl")
        else:
            error_msg = resp.json().get("error", {}).get("message", resp.text)
            raise RuntimeError(f"Erro ao enviar '{file_name}' (HTTP {resp.status_code}): {error_msg}")


# Single instance to reuse the token (singleton)
_client = None

def get_sharepoint_client() -> SharePointApi:
    """
    Singleton factory for the SharePoint client.
    Ensures that the token is reused.
    """
    global _client
    if _client is None:
        _client = SharePointApi() # Create the instance if it does not exist
    return _client # Returns the existing instance