from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from rpg_translator.core.store import Store


class GlossaryDialog(QDialog):
    """翻译前弹出的可编辑术语表：展示自动抽取的候选词和建议译名，用户可以直接改，
    确认后才继续走批量翻译。v1 做最简单的表格展示/编辑版本（见 spec 第 2、10 节）。
    """

    def __init__(self, db_path: Path, glossary: dict[str, str], parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("术语表确认")
        self._db_path = db_path

        self._table = QTableWidget(len(glossary), 2)
        self._table.setHorizontalHeaderLabels(["原文术语", "译名"])
        for row, (term, translation) in enumerate(glossary.items()):
            self._table.setItem(row, 0, QTableWidgetItem(term))
            self._table.setItem(row, 1, QTableWidgetItem(translation))

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(self._table)
        layout.addWidget(buttons)

    def edited_glossary(self) -> dict[str, str]:
        result: dict[str, str] = {}
        for row in range(self._table.rowCount()):
            term_item = self._table.item(row, 0)
            translation_item = self._table.item(row, 1)
            term = term_item.text().strip() if term_item else ""
            translation = translation_item.text().strip() if translation_item else ""
            if term:
                result[term] = translation
        return result

    def _on_accept(self) -> None:
        with Store(self._db_path) as store:
            store.set_glossary(self.edited_glossary())
        self.accept()
