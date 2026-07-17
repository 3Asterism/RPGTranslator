from __future__ import annotations

from dataclasses import dataclass

import httpx


@dataclass
class LLMConfig:
    api_key: str
    base_url: str
    model: str
    timeout: float = 60.0


def _normalize_base_url(base_url: str) -> str:
    return base_url.rstrip("/") + "/"


class LLMClient:
    """OpenAI 兼容协议的聊天补全客户端（面向 DeepSeek，也兼容其他同协议服务商）。

    system prompt（术语表 + 固定指令）放前面、user prompt（本批次待译文本）放后面，
    方便复用同一份 system prompt 时吃到 DeepSeek 的 context caching 折扣。
    """

    def __init__(self, config: LLMConfig):
        self._config = config
        self._http = httpx.AsyncClient(
            base_url=_normalize_base_url(config.base_url),
            headers={"Authorization": f"Bearer {config.api_key}"},
            timeout=config.timeout,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> LLMClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def chat(self, system_prompt: str, user_prompt: str) -> str:
        response = await self._http.post(
            "chat/completions",
            json={
                "model": self._config.model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "stream": False,
            },
        )
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"]
