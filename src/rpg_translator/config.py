from __future__ import annotations

import os

import keyring
from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

load_dotenv()

_KEYRING_SERVICE = "rpg_translator"
_KEYRING_USERNAME = "deepseek_api_key"
_KEYRING_FALLBACK_USERNAME = "fallback_api_key"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-v4-flash"
    concurrency: int = 4
    output_dir: str = "output"

    # 可选的备用 provider：主 provider（DeepSeek）持续报瞬时错误（429/5xx/连接失败）
    # 重试用尽后，LLMClient 会换到这一个继续用。不配置就只用主 provider，行为不变。
    fallback_api_key: str | None = None
    fallback_base_url: str | None = None
    fallback_model: str | None = None


def get_deepseek_api_key() -> str | None:
    """优先读系统凭据管理器（keyring），本地调试兜底读环境变量 DEEPSEEK_API_KEY。"""
    key = keyring.get_password(_KEYRING_SERVICE, _KEYRING_USERNAME)
    if key:
        return key
    return os.environ.get("DEEPSEEK_API_KEY")


def set_deepseek_api_key(key: str) -> None:
    keyring.set_password(_KEYRING_SERVICE, _KEYRING_USERNAME, key)


def get_fallback_api_key() -> str | None:
    """备用 provider 的 Key，同样走 keyring，本地调试兜底读 .env 的 FALLBACK_API_KEY。"""
    key = keyring.get_password(_KEYRING_SERVICE, _KEYRING_FALLBACK_USERNAME)
    if key:
        return key
    return os.environ.get("FALLBACK_API_KEY")


def set_fallback_api_key(key: str) -> None:
    keyring.set_password(_KEYRING_SERVICE, _KEYRING_FALLBACK_USERNAME, key)
