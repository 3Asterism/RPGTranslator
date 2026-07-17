from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from rpg_translator.core.ir import TextUnit


class EngineAdapter(ABC):
    @staticmethod
    @abstractmethod
    def detect(project_dir: Path) -> bool:
        """判断这个目录是不是这个引擎的工程"""

    @abstractmethod
    def extract(self, project_dir: Path) -> list[TextUnit]: ...

    @abstractmethod
    def inject(self, project_dir: Path, units: list[TextUnit], output_dir: Path) -> None:
        """把翻译结果写入 output_dir（不要原地覆盖，输出到新目录）"""
