from typing import List
from pathlib import Path
import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


# get current directory and load .env file and read variables
base_dir: Path = Path(__file__).resolve().parents[2]

dotenv_path = base_dir / "config" / "credentials" / ".env"
yaml_path = base_dir / "config" / "settings" / "config.yml"

# --- 1. Pydantic sub-models for config.yml file ---
class AnticaptchaLinks(BaseModel):
    link_recaptcha_ssw: str = ""

class AnticaptchaConfig(BaseModel):
    links: AnticaptchaLinks = AnticaptchaLinks()

class Bot019(BaseModel):
    list_units: List[str] = []

class Bot056(BaseModel):
    number_reports: List[str] = []

class Bot076(BaseModel):
    list_units: List[str] = []

class Bot081(BaseModel):
    list_units: List[str] = []

class Bot103(BaseModel):
    columns_103: List[str] = []

class Bot441(BaseModel):
    path_download_441: str = ""
    columns_441: List[str] = []

class Bot455CGK(BaseModel):
    path_download_455CGK: str = ""
    columns_455CGK: List[str] = []

class VariablesConfig(BaseModel):
    bot_019: Bot019 = Bot019()
    bot_056: Bot056 = Bot056()
    bot_076: Bot076 = Bot076()
    bot_081: Bot081 = Bot081()
    bot_103: Bot103 = Bot103()
    bot_441: Bot441 = Bot441()
    bot_455CGK: Bot455CGK = Bot455CGK()

class YamlConfig(BaseModel):
    anticaptcha: AnticaptchaConfig = AnticaptchaConfig()
    variables: VariablesConfig = VariablesConfig()


class AppSettings(BaseSettings):
    # Credentials via .env (Pydantic automatically maps env keys)
    ssw_domain: str = Field(alias="SSW_DOMAIN", default="")
    ssw_cpf: str = Field(alias="SSW_CPF", default="")
    ssw_user: str = Field(alias="SSW_USER", default="")
    ssw_password: str = Field(alias="SSW_PASSWORD", default="")
    key_api_recaptcha: str = Field(alias="KEY_API_RECAPTCHA", default="")
    
    # Graph API and Sharepoint (now using the correct environment variable key)
    tenant_id: str = Field(alias="TENANT_ID_SWT268", default="")
    client_id: str = Field(alias="CLIENT_ID_SWT268", default="")
    client_secret: str = Field(alias="CLIENT_SECRET_SWT268", default="")
    sharepoint_domain_name: str = Field(alias="SHAREPOINT_DOMAIN_SWT268", default="")
    sharepoint_site_name: str = Field(alias="SHAREPOINT_SITE_NAME_SWT268", default="")
    sharepoint_library: str = Field(alias="SHAREPOINT_LIBRARY_SWT268", default="")

    # Coupled YAML structure
    yaml_config: YamlConfig = YamlConfig()

    model_config = SettingsConfigDict(
        env_file=str(dotenv_path),
        env_file_encoding="utf-8",
        extra="ignore"
    )

# --- 3. Instantiation and loading ---
def init_settings() -> AppSettings:
    yaml_data = {}
    if yaml_path.exists():
        with open(yaml_path, "r", encoding="utf-8") as f:
            yaml_data = yaml.safe_load(f) or {}
            
    return AppSettings(yaml_config=YamlConfig(**yaml_data))

# Modern global object, typed and with autocomplete!
settings = init_settings()