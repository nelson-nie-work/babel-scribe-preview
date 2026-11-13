"""词库提示胶囊：轻提示可点击，正文输入焦点留在原窗口。"""
import ctypes
from ctypes import wintypes
from typing import Callable

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget


_STYLE = """
QFrame#wordCapsule { background: #1c1c1e; border-radius: 21px; }
QLabel#wordMessage { color: #ffffff; font-size: 13px; }
QLabel#wordCheck { color: #30d158; font-size: 14px; }
QPushButton {
    background: #343438; color: #ffffff; border: none; border-radius: 14px;
    font-size: 12px; padding: 0 9px;
}
QPushButton:hover { background: #4a4a4e; }
QPushButton#wordAction[remember="true"] { background: #0071e3; font-weight: 600; }
QPushButton#wordAction[remember="true"]:hover { background: #0066cc; }
QPushButton#wordSkip { background: transparent; color: #dedee5; padding: 0 6px; }
QPushButton#wordSkip:hover { background: #343438; }
"""


class WordCapsule(QWidget):
    def __init__(self):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setWindowTitle("BABEL Scribe · 词库提示")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setStyleSheet(_STYLE)
        shell = QVBoxLayout(self)
        shell.setContentsMargins(0, 0, 0, 0)
        panel = QFrame()
        panel.setObjectName("wordCapsule")
        panel.setFixedHeight(42)
        shell.addWidget(panel)
        self._row = QHBoxLayout(panel)
        self._row.setContentsMargins(13, 5, 7, 5)
        self._row.setSpacing(7)

        self._check = QLabel("✓")
        self._check.setObjectName("wordCheck")
        self._row.addWidget(self._check)
        self._message = QLabel()
        self._message.setObjectName("wordMessage")
        self._message.setTextFormat(Qt.TextFormat.PlainText)
        self._row.addWidget(self._message)
        self._action = QPushButton()
        self._action.setObjectName("wordAction")
        self._action.setFixedHeight(28)
        self._action.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._action.clicked.connect(self._choose)
        self._row.addWidget(self._action)
        self._skip = QPushButton("略过")
        self._skip.setObjectName("wordSkip")
        self._skip.setFixedHeight(28)
        self._skip.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._skip.clicked.connect(self.dismiss)
        self._row.addWidget(self._skip)

        self._callback = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.dismiss)

    def suggest(self, term: str, remember: Callable[[], None]) -> None:
        self._present(f"记住「{term}」？", "记住", remember, suggestion=True)

    def saved(self, term: str, undo: Callable[[], None]) -> None:
        self._present(f"已记住「{term}」", "撤销", undo, saved=True)

    def notice(self, message: str) -> None:
        self._present(message)

    def dismiss(self) -> None:
        self._timer.stop()
        self._callback = None
        self.hide()

    def _choose(self) -> None:
        callback = self._callback
        self.dismiss()
        if callback is not None:
            callback()

    def _present(self, text: str, action: str = "", callback: Callable[[], None] | None = None,
                 *, suggestion: bool = False, saved: bool = False) -> None:
        self._callback = callback
        self._message.setText(text)
        self._check.setVisible(saved)
        self._skip.setVisible(suggestion)
        self._action.setVisible(bool(action))
        self._action.setText(action)
        self._action.setProperty("remember", suggestion)
        self._action.style().unpolish(self._action)
        self._action.style().polish(self._action)
        self._row.invalidate()
        self.setFixedSize(self._row.sizeHint().width(), 42)
        self.layout().activate()
        self._row.activate()
        area = QGuiApplication.primaryScreen().availableGeometry()
        self.move(area.x() + (area.width() - self.width()) // 2, area.bottom() - self.height() - 48)
        self.show()
        if QGuiApplication.platformName() == "windows":
            hwnd = wintypes.HWND(int(self.winId()))
            user32 = ctypes.windll.user32
            if not user32.IsWindowVisible(hwnd):
                user32.ShowWindow(hwnd, 4)  # SW_SHOWNOACTIVATE
        self._timer.start(11000 if suggestion else 5500 if saved else 1800)
