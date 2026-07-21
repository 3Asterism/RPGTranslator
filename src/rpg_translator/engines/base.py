from __future__ import annotations

import shutil
from abc import ABC, abstractmethod
from pathlib import Path

from rpg_translator.core.ir import TextUnit


def copy_project_if_different(project_dir: Path, output_dir: Path) -> None:
    """inject() 的第一步：把 project_dir 复制一份到 output_dir 再改文本。原地注入
    （output_dir 就是 project_dir 本身）时跳过这步——shutil.copytree 对同一个目录
    自拷贝会报错（尝试把文件复制到它自己身上），而且原地场景下本来就不需要复制。"""
    if output_dir.resolve() != project_dir.resolve():
        shutil.copytree(project_dir, output_dir, dirs_exist_ok=True)


class EngineAdapter(ABC):
    @staticmethod
    @abstractmethod
    def detect(project_dir: Path) -> bool:
        """判断这个目录是不是这个引擎的工程"""

    @abstractmethod
    def extract(self, project_dir: Path) -> list[TextUnit]: ...

    @abstractmethod
    def inject(self, project_dir: Path, units: list[TextUnit], output_dir: Path) -> None:
        """把翻译结果写入 output_dir。output_dir 等于 project_dir 时是原地注入——
        不复制到新目录，直接改写游戏工程本身（原文由 pipeline.run_inject 在覆盖前
        另行备份，用于一键切换回原文，不是这一层的职责）。"""
