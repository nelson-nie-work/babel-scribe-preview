"""词库概念稿的学词确认窗。词库只由调用方在确认后写入。"""
import ctypes
from ctypes import wintypes
from html import escape
from typing import Callable

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (QApplication, QDialog, QFrame, QGraphicsDropShadowEffect,
                               QHBoxLayout, QLabel, QLayout, QLineEdit, QPushButton,
                               QVBoxLayout, QWidget)

from scribe.learning import valid_word
from scribe.tray import make_icon


_STYLE = """
QLabel { color: #1d1d1f; }
QFrame#learnPanel {
    background: #f5f5f7;
    border: 1px solid #d1d1d5;
    border-radius: 12px;
}
QFrame#learnHeader {
    background: #ececf0;
    border-bottom: 1px solid #dadae0;
    border-top-left-radius: 11px;
    border-top-right-radius: 11px;
}
QLabel#learnBrand { font-weight: 600; }
QLabel#learnLocation, QLabel#learnSubtitle, QLabel#learnContextLine { color: #626269; }
QLabel#learnTitle { font-size: 22px; font-weight: 700; }
QFrame#learnFields {
    background: #ffffff;
    border: 1px solid #e5e5ea;
    border-radius: 10px;
}
QLabel#learnFieldLabel { color: #626269; font-size: 11px; font-weight: 600; }
QLabel#learnArrow { color: #77777e; font-size: 15px; }
QLineEdit {
    background: #ffffff;
    color: #1d1d1f;
    border: 1px solid #c7c7cc;
    border-radius: 7px;
    padding: 0 8px;
    font-size: 14px;
    placeholder-text-color: #727279;
    selection-background-color: #0071e3;
    selection-color: #ffffff;
}
QLineEdit:focus { border-color: #0071e3; }
QLineEdit#learnTerm { font-weight: 600; }
QPushButton {
    color: #1d1d1f;
    background: #ffffff;
    border: 1px solid #c7c7cc;
    border-radius: 7px;
    padding: 0 11px;
}
QPushButton:hover { background: #ececf0; }
QPushButton:focus { border-color: #0071e3; }
QPushButton#learnClose {
    color: #3a3a3c;
    background: transparent;
    border: 1px solid transparent;
    border-radius: 6px;
    padding: 0;
    font-size: 17px;
}
QPushButton#learnClose:hover { background: #e0e0e6; }
QPushButton#learnContextToggle {
    color: #0066cc;
    background: transparent;
    border: 1px solid transparent;
    border-radius: 3px;
    padding: 0;
    text-align: left;
}
QPushButton#learnContextToggle:hover { background: #e8f2fc; }
QFrame#learnFooter { border-top: 1px solid #e5e5ea; }
QPushButton#learnSave {
    color: #ffffff;
    background: #0071e3;
    border-color: #0071e3;
    font-weight: 600;
}
QPushButton#learnSave:hover { background: #0066cc; border-color: #0066cc; }
QPushButton#learnSave:disabled { color: #727279; background: #e9e9ee; border-color: #dddde2; }
"""


class _Header(QFrame):
    def mousePressEvent(self, event):
        window = self.window().windowHandle()
        if event.button() == Qt.MouseButton.LeftButton and window and window.startSystemMove():
            event.accept()
        else:
            super().mousePressEvent(event)


def _label(text: str, name: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName(name)
    return label


def _button(text: str, name: str) -> QPushButton:
    button = QPushButton(text)
    button.setObjectName(name)
    button.setAutoDefault(False)
    return button


def _context_text(text: str, word: str) -> str:
    excerpt = text[:180] + ("…" if len(text) > 180 else "")
    before, match, after = excerpt.partition(word) if word else (excerpt, "", "")
    return (escape(before)
            + (f'<span style="background:#e8f2fc;color:#0057b0">{escape(match)}</span>' if match else "")
            + escape(after))


class LearnDialog(QDialog):
    def __init__(self, alias: str, term: str, original: str, selected: str, *, history: bool = False):
        super().__init__(None, Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint)
        self.setObjectName("learnDialog")
        self.setWindowTitle("BABEL Scribe · 学习一个词")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        font = QFont()
        font.setFamilies(["Microsoft YaHei UI", "Segoe UI"])
        font.setPixelSize(12)
        self.setFont(font)
        self.setStyleSheet(_STYLE)
        self._history_original = original if history else None

        selected_word = selected.strip()
        if not alias and not term and valid_word(selected_word):
            term = selected_word

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 16, 24, 28)
        outer.setSizeConstraint(QLayout.SizeConstraint.SetFixedSize)
        panel = QFrame()
        panel.setObjectName("learnPanel")
        panel.setFixedWidth(440)
        shadow = QGraphicsDropShadowEffect(panel)
        shadow.setBlurRadius(32)
        shadow.setOffset(0, 7)
        shadow.setColor(QColor(28, 28, 30, 55))
        panel.setGraphicsEffect(shadow)
        outer.addWidget(panel)
        stack = QVBoxLayout(panel)
        stack.setContentsMargins(1, 1, 1, 1)
        stack.setSpacing(0)
        stack.addWidget(self._header())

        body = QWidget()
        content = QVBoxLayout(body)
        content.setContentsMargins(21, 18, 21, 16)
        content.setSpacing(0)
        title = _label("改正并学习" if history else "学习一个词", "learnTitle")
        title.setFixedHeight(30)
        content.addWidget(title)
        if history:
            hint = "填写这条听写里的错词，原听写记录会保留。"
        elif alias:
            hint = "确认后，下次听写会用到这条纠错。"
        elif term:
            hint = "已带入选中的词，请补上实际错词。"
        else:
            hint = "请填写实际错词和正确写法。"
        subtitle = _label(hint, "learnSubtitle")
        subtitle.setFixedHeight(18)
        content.addSpacing(2)
        content.addWidget(subtitle)
        content.addSpacing(15)
        self.alias_edit = QLineEdit(alias)
        self.alias_edit.setObjectName("learnAlias")
        self.alias_edit.setPlaceholderText("输入实际错词")
        self.term_edit = QLineEdit(term)
        self.term_edit.setObjectName("learnTerm")
        self.term_edit.setPlaceholderText("输入正确写法")
        content.addWidget(self._fields())
        if original or selected:
            content.addSpacing(10)
            self._add_context(content, original, selected, alias, term, history=history)
        stack.addWidget(body)
        stack.addWidget(self._footer())

        self.alias_edit.textChanged.connect(self._update_save)
        self.term_edit.textChanged.connect(self._update_save)
        if alias and history:
            self.term_edit.setFocus()
        elif alias:
            panel.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
            panel.setFocus()
        else:
            self.alias_edit.setFocus()
        self._update_save()

    def _header(self) -> QFrame:
        header = _Header()
        header.setObjectName("learnHeader")
        header.setFixedHeight(46)
        row = QHBoxLayout(header)
        row.setContentsMargins(13, 0, 13, 0)
        row.setSpacing(9)
        icon = QLabel()
        icon.setFixedSize(25, 25)
        icon.setPixmap(make_icon().pixmap(25, 25))
        row.addWidget(icon)
        row.addWidget(_label("语音键盘", "learnBrand"))
        row.addWidget(_label("· 词库", "learnLocation"))
        row.addStretch()
        close = _button("×", "learnClose")
        close.setAccessibleName("关闭")
        close.setFixedSize(27, 27)
        close.clicked.connect(self.reject)
        row.addWidget(close)
        return header

    def _fields(self) -> QFrame:
        fields = QFrame()
        fields.setObjectName("learnFields")
        row = QHBoxLayout(fields)
        row.setContentsMargins(13, 12, 13, 13)
        row.setSpacing(10)
        for name, edit in (("常被听成", self.alias_edit), ("正确写法", self.term_edit)):
            if edit is self.term_edit:
                arrow = _label("→", "learnArrow")
                arrow.setFixedSize(18, 35)
                arrow.setAlignment(Qt.AlignmentFlag.AlignCenter)
                row.addWidget(arrow, 0, Qt.AlignmentFlag.AlignBottom)
            label = _label(name, "learnFieldLabel")
            label.setBuddy(edit)
            edit.setAccessibleName(name)
            edit.setFixedHeight(35)
            field = QVBoxLayout()
            field.setSpacing(5)
            field.addWidget(label)
            field.addWidget(edit)
            row.addLayout(field, 1)
        return fields

    def _add_context(self, content: QVBoxLayout, original: str, selected: str,
                     alias: str, term: str, *, history: bool = False) -> None:
        toggle = _button("▸  听写上下文", "learnContextToggle")
        toggle.setAccessibleName("听写上下文")
        toggle.setFixedHeight(20)
        toggle.setCheckable(True)
        content.addWidget(toggle, 0, Qt.AlignmentFlag.AlignLeft)
        context = QWidget()
        context.setObjectName("learnContext")
        examples = QVBoxLayout(context)
        examples.setContentsMargins(12, 6, 0, 0)
        examples.setSpacing(5)
        for title, text, word in (("最终文字" if history else "刚才听写", original, alias),
                                  ("选中的词", selected, term)):
            if text:
                html = f"<b>{title}</b>　<span style='color:#1d1d1f'>{_context_text(text, word)}</span>"
                line = _label(html, "learnContextLine")
                line.setWordWrap(True)
                line.setTextFormat(Qt.TextFormat.RichText)
                examples.addWidget(line)
        content.addWidget(context)
        context.hide()

        def expand(opened: bool) -> None:
            toggle.setText(("▾" if opened else "▸") + "  听写上下文")
            context.setVisible(opened)
            content.parentWidget().updateGeometry()

        toggle.toggled.connect(expand)
        toggle.setChecked(history)

    def _footer(self) -> QFrame:
        footer = QFrame()
        footer.setObjectName("learnFooter")
        row = QHBoxLayout(footer)
        row.setContentsMargins(21, 10, 21, 12)
        row.setSpacing(8)
        row.addStretch()
        cancel = _button("取消", "learnCancel")
        cancel.setFixedSize(48, 31)
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)
        self.save_button = _button("加入词库", "learnSave")
        self.save_button.setFixedSize(73, 31)
        self.save_button.clicked.connect(self.accept)
        row.addWidget(self.save_button)
        return footer

    def values(self) -> tuple[str, str]:
        return self.alias_edit.text().strip(), self.term_edit.text().strip()

    def _update_save(self) -> None:
        alias, term = self.values()
        in_history = self._history_original is None or alias.casefold() in self._history_original.casefold()
        self.save_button.setEnabled(in_history and alias.casefold() != term.casefold()
                                    and valid_word(alias) and valid_word(term))


def reveal_learning_dialog(dialog) -> bool:
    """处理 Qt 认为已显示、但 Windows 实际隐藏的窗口。"""
    if not dialog.isVisible():
        dialog.show()
    if QApplication.platformName() == "windows":
        hwnd = wintypes.HWND(int(dialog.winId()))
        user32 = ctypes.windll.user32
        if not user32.IsWindowVisible(hwnd):
            user32.ShowWindow(hwnd, 4)  # SW_SHOWNOACTIVATE
        if not user32.IsWindowVisible(hwnd):
            return False
    dialog.raise_()
    dialog.activateWindow()
    return True


def confirm_alias(alias: str, term: str, *, original: str = "", selected: str = "",
                  on_shown: Callable[[bool], None] = lambda visible: None,
                  history: bool = False) -> tuple[str, str] | None:
    dialog = LearnDialog(alias, term, original, selected, history=history)
    reveal = QTimer(dialog)
    reveal.setSingleShot(True)
    reveal.timeout.connect(lambda: on_shown(reveal_learning_dialog(dialog)))
    reveal.start(0)
    result = dialog.exec()
    reveal.stop()
    if result == QDialog.DialogCode.Accepted and dialog.save_button.isEnabled():
        return dialog.values()
    return None
