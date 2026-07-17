from __future__ import annotations

import os

import keyring
from pydantic_settings import BaseSettings, SettingsConfigDict

_KEYRING_SERVICE = "rpg_translator"
_KEYRING_USERNAME = "deepseek_api_key"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-v4-flash"
    concurrency: int = 4
    output_dir: str = "output"


def get_deepseek_api_key() -> str | None:
    """优先读系统凭据管理器（keyring），本地调试兜底读环境变量 DEEPSEEK_API_KEY。"""
    key = keyring.get_password(_KEYRING_SERVICE, _KEYRING_USERNAME)
    if key:
        return key
    return os.environ.get("DEEPSEEK_API_KEY")


def set_deepseek_api_key(key: str) -> None:
    keyring.set_password(_KEYRING_SERVICE, _KEYRING_USERNAME, key)
