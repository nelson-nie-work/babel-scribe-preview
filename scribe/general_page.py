"""主窗口的通用设置。应用改动成功后才更新控件。"""
from collections.abc import Callable

from PySide6.QtCore import QPointF, QSignalBlocker, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea,
    QSizePolicy, QStyle, QStyleOptionComboBox, QVBoxLayout, QWidget,
)

from scribe.settings import HOTKEYS
from scribe.vocab_window import _label


class _SettingsCombo(QComboBox):
    def __init__(self):
        super().__init__(objectName="settingCombo")

    def paintEvent(self, event):
        super().paintEvent(event)
        option = QStyleOptionComboBox()
        self.initStyleOption(option)
        arrow = self.style().subControlRect(QStyle.ComplexControl.CC_ComboBox, option,
                                            QStyle.SubControl.SC_ComboBoxArrow, self)
        x, y = arrow.center().x(), arrow.center().y()
        with QPainter(self) as painter:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            color = "#484852" if self.isEnabled() else "#85858f"
            painter.setPen(QPen(QColor(color), 1.25, Qt.PenStyle.SolidLine,
                                Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            painter.drawPolyline(QPolygonF([QPointF(x - 4, y - 2), QPointF(x, y + 2),
                                            QPointF(x + 4, y - 2)]))


class _Switch(QCheckBox):
    def __init__(self, title: str):
        super().__init__()
        self.setAccessibleName(title)
        self.setFixedSize(38, 23)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)

    def hitButton(self, pos):
        return self.rect().contains(pos)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        checked = self.isChecked()
        painter.setPen(QPen(QColor("#003d7b" if checked else "#393a43"), 2)
                       if self.hasFocus() else Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#0069d4" if checked else "#7a7b86"))
        painter.drawRoundedRect(2, 2, 34, 19, 9, 9)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("white"))
        painter.drawEllipse(19 if checked else 4, 4, 15, 15)


class _ShortcutDialog(QDialog):
    def __init__(self, key: str, parent: QWidget):
        super().__init__(parent, Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("vocabConfirm")
        self.setWindowTitle("更改听写快捷键")
        self.setMinimumWidth(376)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 15)
        layout.addWidget(_label("更改听写快捷键", "vocabTitle"))
        layout.addWidget(_label("听写和免手持共用一个键。收词仍用右 Ctrl + L。", "vocabQuiet"))
        self.pending = _label("", "mainSection")
        layout.addWidget(self.pending)
        choices = QHBoxLayout()
        self.options = {}
        for name, (title, _code) in HOTKEYS.items():
            button = QPushButton(title, checkable=True, autoDefault=False)
            button.clicked.connect(lambda _checked=False, option=name: self._select(option))
            self.options[name] = button
            choices.addWidget(button)
        layout.addLayout(choices)
        self.error = _label("", "vocabError")
        layout.addWidget(self.error)
        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel = QPushButton("取消", autoDefault=False)
        cancel.clicked.connect(self.reject)
        self.save = QPushButton("保存快捷键", objectName="vocabSave", autoDefault=False)
        self.save.clicked.connect(self.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(self.save)
        layout.addLayout(buttons)
        self._select(key)
        cancel.setFocus()

    def _select(self, key: str):
        self.key = key
        self.pending.setText(f"候选键  {HOTKEYS[key][0]}")
        for name, button in self.options.items():
            button.setChecked(name == key)
        self.error.clear()
        self.save.setEnabled(True)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Escape, Qt.Key.Key_Tab, Qt.Key.Key_Backtab, Qt.Key.Key_Space,
                           Qt.Key.Key_Return, Qt.Key.Key_Enter):
            super().keyPressEvent(event)
            return
        code = event.nativeVirtualKey()
        if event.key() == Qt.Key.Key_Control and code not in (0xA2, 0xA3):
            code = 0xA3 if event.nativeScanCode() & 0x100 else 0xA2
        elif event.key() == Qt.Key.Key_Alt and code != 0xA5:
            code = 0xA4 if not event.nativeScanCode() & 0x100 else 0xA5
        elif event.key() == Qt.Key.Key_F8:
            code = 0x77
        modifiers = event.modifiers()
        if code in (0xA2, 0xA3):
            modifiers &= ~Qt.KeyboardModifier.ControlModifier
        elif code == 0xA4:
            modifiers &= ~Qt.KeyboardModifier.AltModifier
        key = next((key for key, (_label_text, vk) in HOTKEYS.items() if vk == code), None)
        if key and not modifiers:
            self._select(key)
        else:
            self.error.setText("请选择上方的单个键。")
            self.save.setEnabled(False)
        event.accept()


class GeneralPage(QScrollArea):
    def __init__(self, prefs: Callable[[], dict], update: Callable[[str, object], None],
                 microphones: Callable[[], list[str]], startup: Callable[[], bool],
                 can_change_key: Callable[[], bool]):
        super().__init__(objectName="mainScroll", widgetResizable=True)
        self.prefs, self.update = prefs, update
        self.microphones, self.startup, self.can_change_key = microphones, startup, can_change_key
        self.capturing = False
        self.controls = {}
        body = QWidget(objectName="mainScrollContent")
        self.setWidget(body)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(28, 20, 28, 17)
        layout.setSpacing(6)
        layout.addWidget(_label("通用", "vocabHeading"))
        layout.addWidget(_label("听写方式、文字整理和后台运行。", "vocabQuiet"))

        group = self._group(layout, "听写")
        shortcut = self._row(group, "听写快捷键", "按住说话，双击进入免手持模式。")
        self.key_label = _label("", "shortcutBadge")
        shortcut.addWidget(self.key_label)
        change = QPushButton("更改")
        change.clicked.connect(self._change_key)
        shortcut.addWidget(change)
        self.mic = self._combo(group, "microphone", "麦克风", (), "跟随 Windows，或指定一个设备。")

        group = self._group(layout, "识别与整理")
        self.engine_hint = self._toggle(group, "dual_engine", "双引擎识别", "SenseVoice 与 Whisper 一起识别。")
        self._toggle(group, "smart_polish", "智能整理", "需要整理时发送转写文字和相关词条，录音留在本机。")
        self._combo(group, "style", "整理程度", (("punctuation", "只加标点"), ("natural", "自然"), ("formal", "书面")))
        self._combo(group, "llm_mode", "整理服务", (("gpt", "GPT"), ("deepseek", "DeepSeek"), ("auto", "自动")))

        group = self._group(layout, "后台运行")
        self._toggle(group, "startup", "开机启动", "登录 Windows 后在托盘启动 Scribe。")
        self._toggle(group, "idle_release", "闲置 30 分钟释放模型", "下次听写重新加载，录音照常开始。")
        status_line = QHBoxLayout()
        status_line.setContentsMargins(1, 3, 0, 0)
        status_line.setSpacing(6)
        self.status_dot = QWidget()
        self.status_dot.setFixedSize(7, 7)
        status_line.addWidget(self.status_dot, 0, Qt.AlignmentFlag.AlignVCenter)
        self.status = _label("改动自动保存，仅在这台电脑生效。", "vocabQuiet")
        status_line.addWidget(self.status, 1)
        layout.addLayout(status_line)
        self._set_status(self.status.text())
        layout.addStretch()

    @staticmethod
    def _group(layout, title):
        layout.addSpacing(4)
        layout.addWidget(_label(title, "mainSection"))
        panel = QFrame(objectName="mainInset")
        rows = QVBoxLayout(panel)
        rows.setContentsMargins(12, 0, 12, 0)
        rows.setSpacing(0)
        layout.addWidget(panel)
        return rows

    @staticmethod
    def _row(rows, title, hint=""):
        if rows.count():
            divider = QFrame(objectName="vocabDivider")
            divider.setFrameShape(QFrame.Shape.HLine)
            rows.addWidget(divider)
        row = QHBoxLayout()
        row.setContentsMargins(0, 6, 0, 6)
        words = QVBoxLayout()
        words.setSpacing(1)
        words.addWidget(_label(title, "settingLabel"))
        if hint:
            label = _label(hint, "vocabQuiet")
            label.setWordWrap(True)
            words.addWidget(label)
        row.addLayout(words, 1)
        rows.addLayout(row)
        return row

    def _combo(self, group, key, title, choices, hint=""):
        row = self._row(group, title, hint)
        combo = _SettingsCombo()
        combo.setAccessibleName(title)
        combo.setFixedWidth(158)
        combo.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        for value, label in choices:
            combo.addItem(label, value)
        combo.activated.connect(lambda _index: self._change(key, combo.currentData()))
        row.addWidget(combo)
        self.controls[key] = combo
        return combo

    def _toggle(self, group, key, title, hint):
        row = self._row(group, title, hint)
        switch = _Switch(title)
        switch.clicked.connect(lambda checked: self._change(key, checked))
        row.addWidget(switch)
        self.controls[key] = switch
        return row.itemAt(0).layout().itemAt(1).widget()

    def refresh(self):
        prefs = self.prefs()
        names = self.microphones()
        with QSignalBlocker(self.mic):
            self.mic.clear()
            self.mic.addItem("系统默认", "")
            for name in names:
                self.mic.addItem(name, name)
            if prefs["microphone"] and prefs["microphone"] not in names:
                self.mic.addItem(f"不可用 · {prefs['microphone']}", prefs["microphone"])
        self.sync()

    def sync(self):
        prefs = self.prefs()
        self.key_label.setText(HOTKEYS[prefs["hotkey"]][0])
        self.engine_hint.setText("SenseVoice 与 Whisper 一起识别。" if prefs["dual_engine"]
                                 else "使用 SenseVoice 单引擎识别。")
        for key, control in self.controls.items():
            with QSignalBlocker(control):
                if isinstance(control, QComboBox):
                    control.setCurrentIndex(control.findData(prefs[key]))
                else:
                    control.setChecked(self.startup() if key == "startup" else prefs[key])
        for key in ("style", "llm_mode"):
            self.controls[key].setEnabled(prefs["smart_polish"])

    def _set_status(self, message, *, error=False):
        self.status.setText(message)
        color = "#a6252b" if error else "#28784f"
        self.status_dot.setStyleSheet(f"background-color: {color}; border-radius: 3px;")

    def _change(self, key, value):
        try:
            self.update(key, value)
        except (OSError, ValueError) as exc:
            self._set_status(str(exc) if isinstance(exc, ValueError) else "设置未保存，请重试。", error=True)
        else:
            if isinstance(value, bool):
                action = "开启" if value else "关闭"
                self._set_status(f"已{action}{self.controls[key].accessibleName()}。")
            else:
                self._set_status("已保存，仅在这台电脑生效。")
        self.sync()

    def _change_key(self):
        if not self.can_change_key():
            self._set_status("等本次听写结束后，再更改快捷键。", error=True)
            return
        dialog = _ShortcutDialog(self.prefs()["hotkey"], self)
        self.capturing = True
        try:
            if dialog.exec() == QDialog.DialogCode.Accepted:
                self._change("hotkey", dialog.key)
        finally:
            self.capturing = False
            dialog.deleteLater()
