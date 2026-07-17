from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from rpg_translator.core.ir import EngineName, TextUnit
from rpg_translator.engines._rgss_common import RGSSAdapterBase
from rpg_translator.engines._vxace_message_patch import (
    RUNTIME_LINE_WRAP_SCRIPT_NAME,
    RUNTIME_LINE_WRAP_SOURCE,
)
from rpg_translator.engines._vxace_scripts import (
    ScriptsFormatError,
    append_script,
    has_conflicting_message_system,
    read_scripts,
)

# RPG::Actors/Classes/... の実データを直接検証できる VX Ace プロジェクトが手元にないため、
# フィールド名は公開の RGSS3 参照実装（bluepixelmike/rpg-maker-rgss、非公式コミュニティ製）
# から確認したもの。実際のゲームで挙動が違えばここを直す必要がある。
#
# 2026-07 补充：找到一个真实、可运行的 VX Ace 工程（RTP + Game.exe + RGSS301.dll
# 齐全）实测过一遍——@name/@nickname/@description 等字段名和上面这份非官方参考
# 实现完全对得上，数据库文本抽取没有问题（详见 galgame_rpgmaker_translator_spec
# M4.9 验收记录）。


class VXAceAdapter(RGSSAdapterBase):
    engine_name: ClassVar[EngineName] = "vxace"
    data_dir: ClassVar[str] = "Data"
    file_extension: ClassVar[str] = ".rvdata2"
    database_files: ClassVar[list[str]] = [
        "Actors.rvdata2",
        "Classes.rvdata2",
        "Skills.rvdata2",
        "Items.rvdata2",
        "Weapons.rvdata2",
        "Armors.rvdata2",
        "Enemies.rvdata2",
        "States.rvdata2",
    ]

    @staticmethod
    def detect(project_dir: Path) -> bool:
        return (project_dir / "Data" / "Actors.rvdata2").is_file() or (
            project_dir / "Game.rvproj2"
        ).is_file()

    def _after_inject(self, output_dir: Path, units: list[TextUnit]) -> None:
        """spec 9.2.b：把消息框运行时像素级换行补丁注入 Scripts.rvdata2。

        只在真的有翻译内容时才动手（纯预览/未翻译的 inject 不该改动
        Scripts.rvdata2，保持 M1/M4 的"未翻译回填逐字节不变"回归校验成立），
        且工程里没有 Scripts.rvdata2（比如测试用的合成 fixture）或者读不出来、
        或者检测到已有第三方消息系统脚本，都直接跳过、静默降级到已有的
        `rewrap_paragraph` 估算重排方案——这一步是锦上添花，不该因为补丁本身
        的问题连累核心的文本回填流程。
        """
        if not any(u.translated_text is not None for u in units):
            return
        scripts_path = output_dir / self.data_dir / "Scripts.rvdata2"
        if not scripts_path.is_file():
            return
        try:
            entries = read_scripts(scripts_path)
        except ScriptsFormatError:
            return
        if has_conflicting_message_system(entries):
            return
        next_id = max((e.id for e in entries), default=0) + 1
        append_script(scripts_path, next_id, RUNTIME_LINE_WRAP_SCRIPT_NAME, RUNTIME_LINE_WRAP_SOURCE)
