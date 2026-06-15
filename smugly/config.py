import json
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SMUGLY_", env_file=".env", extra="ignore")

    photo_dir: Path = Path("/photos")
    config_dir: Path = Path("/config")
    smugmug_api_key: str = ""
    smugmug_api_secret: str = ""
    port: int = 8080


settings = Settings()


def _config_file() -> Path:
    return settings.config_dir / "app_config.json"


def load_app_config() -> dict:
    p = _config_file()
    if p.exists():
        return json.loads(p.read_text())
    return {}


def save_app_config(data: dict) -> None:
    settings.config_dir.mkdir(parents=True, exist_ok=True)
    _config_file().write_text(json.dumps(data, indent=2))


def get_db_path() -> Path:
    cfg = load_app_config()
    db_name = cfg.get("db_name", ".sync_state.db")
    return settings.photo_dir / db_name


def load_tokens() -> dict:
    p = settings.config_dir / "tokens.json"
    if p.exists():
        return json.loads(p.read_text())
    return {}


def save_tokens(tokens: dict) -> None:
    settings.config_dir.mkdir(parents=True, exist_ok=True)
    (settings.config_dir / "tokens.json").write_text(json.dumps(tokens, indent=2))


def load_oauth_temp() -> dict:
    p = settings.config_dir / "oauth_temp.json"
    if p.exists():
        return json.loads(p.read_text())
    return {}


def save_oauth_temp(data: dict) -> None:
    settings.config_dir.mkdir(parents=True, exist_ok=True)
    (settings.config_dir / "oauth_temp.json").write_text(json.dumps(data))


def clear_oauth_temp() -> None:
    p = settings.config_dir / "oauth_temp.json"
    if p.exists():
        p.unlink()
