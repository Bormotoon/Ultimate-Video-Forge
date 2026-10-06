"""Phase-one Studio desktop shell over the shared CLI and project model."""

from __future__ import annotations

import sys
from importlib.resources import files
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from studio.core.project import Project
from studio.gui.bridge import RunnerBridge


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Studio")
        self.resize(1100, 720)
        self.bridge = RunnerBridge(self)
        self.source: Path | None = None
        self.navigation = QListWidget()
        self.navigation.addItems(["Start", "Material", "Plan", "Work", "Result"])
        self.pages = QStackedWidget()
        self.material = QTreeWidget()
        self.material.setHeaderLabels(["Asset", "Role", "Group"])
        self.plan_label = QLabel("Scan a folder to build the plan.")
        self.log = QLabel("")
        self.log.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.log.setWordWrap(True)
        self.result = QLabel("No results yet.")
        self._build_pages()
        layout = QHBoxLayout()
        layout.addWidget(self.navigation, 1)
        layout.addWidget(self.pages, 4)
        central = QWidget()
        central.setLayout(layout)
        self.setCentralWidget(central)
        self.navigation.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.navigation.setCurrentRow(0)
        self.bridge.output.connect(self._append_log)
        self.bridge.finished.connect(self._finished)

    def _build_pages(self) -> None:
        start = QWidget()
        start_layout = QVBoxLayout(start)
        title = QLabel("Turn raw recordings into an editable project")
        title.setObjectName("hero")
        choose = QPushButton("Choose material folder")
        choose.clicked.connect(self._choose)
        start_layout.addWidget(title)
        start_layout.addWidget(choose)
        start_layout.addStretch()
        self.pages.addWidget(start)
        self.pages.addWidget(self.material)
        self.pages.addWidget(self._page(self.plan_label, "Build plan", self._plan))
        self.pages.addWidget(self._page(self.log, "Cancel", self.bridge.cancel))
        self.pages.addWidget(self._page(self.result, "Open export folder", self._open_result))

    def _page(self, content: QWidget, action: str, callback: object) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(content)
        button = QPushButton(action)
        button.clicked.connect(callback)  # type: ignore[arg-type]
        layout.addWidget(button)
        return page

    def _choose(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "Choose material folder")
        if not selected:
            return
        self.source = Path(selected)
        self.navigation.setCurrentRow(3)
        self.bridge.start("scan", self.source)

    def _plan(self) -> None:
        if self.source:
            self.navigation.setCurrentRow(3)
            self.bridge.start("plan", self.source)

    def _finished(self, code: int) -> None:
        if not self.source or code:
            return
        path = self.source / "_studio" / "project.json"
        if path.is_file():
            project = Project.load(path)
            self.material.clear()
            for asset in project.assets:
                QTreeWidgetItem(
                    self.material,
                    [str(asset.path), asset.role.value, asset.group_id or ""],
                )
            self.result.setText(str(project.outputs))
            self.navigation.setCurrentRow(1)

    def _append_log(self, text: str) -> None:
        self.log.setText(self.log.text() + text)

    def _open_result(self) -> None:
        if self.source:
            folder = self.source / "_studio" / "export"
            QFileDialog.getOpenFileName(self, "Studio export", str(folder))


def main() -> int:
    app = QApplication(sys.argv)
    stylesheet = files("studio.gui").joinpath("theme.qss").read_text(encoding="utf-8")
    app.setStyleSheet(stylesheet)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
