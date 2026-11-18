"""设置页的整理服务列表与编辑对话框(spec 2026-11-18 整理服务配置)。

界面只显示密钥末 4 位;密钥不进日志。
"""
from collections.abc import Callable
from typing import Optional

from PySide6.QtWidgets import (
    QComboBox, QDialog, QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton,
    QVBoxLayout, QWidget,
)

from scribe.keys import PRESETS, missing_field
from scribe.vocab_window import _label

STATUS_TEXT = {
    "ok": "已连接 {s} 秒", "auth": "密钥无效", "address": "地址不通", "model": "模型不存在",
    "timeout": "超时", "bad_reply": "返回异常", "missing_key": "未填密钥", "missing_field": "未填地址或模型",
    None: "未测试",
}
FAILURE_HINT = {
    "auth": "服务商拒绝了这个密钥。检查是否复制完整。",
    "address": "连不上这个地址。检查接口地址和网络。",
    "model": "服务商没有这个模型名。",
    "timeout": "10 秒内没有回应。",
    "bad_reply": "服务有回应，但内容不对。换个模型试试。",
    "missing_key": "还没填 API 密钥。",
    "missing_field": "接口地址和模型都要填。",
}
SLOW_HINT = "已连接 {s} 秒，但比听写时的等待上限慢，实际听写多半会退回本机识别。换个更快的模型试试。"
LIST_HINT = "按顺序使用，前一个失败或超时就换下一个。"
EMPTY_TEXT = "还没有整理服务。添加一个后，智能整理才会发送文字。"
UNREADABLE_TEXT = "之前保存的整理服务无法读取，可能换了 Windows 账户。旧文件已改名保留，请重新添加。"
STORAGE_NOTE = "密钥用 Windows 加密保存在这台电脑。"


def status_text(status) -> str:
    if status is None:
        return STATUS_TEXT[None]
    if status.kind == "ok":
        text = STATUS_TEXT["ok"].format(s="%.1f" % ((status.ms or 0) / 1000))
        return text + " · 偏慢" if status.slow else text
    return STATUS_TEXT.get(status.kind, STATUS_TEXT["bad_reply"])


def masked(key: str) -> str:
    key = (key or "").strip()
    return "" if not key else ("••••" + key[-4:] if len(key) > 4 else "••••")


class ServiceDialog(QDialog):
    def __init__(self, entry: Optional[dict], test: Callable, parent: QWidget | None = None):
        super().__init__(parent)
        self.entry = dict(entry) if entry else None
        self._test = test
        self.deleted = False
        self.setWindowTitle("编辑整理服务" if entry else "添加整理服务")
        self.setMinimumWidth(420)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.preset_combo = QComboBox()
        for key, preset in PRESETS.items():
            self.preset_combo.addItem(preset.label, key)
        self.name_edit = QLineEdit()
        self.url_edit = QLineEdit()
        self.model_edit = QLineEdit()
        self.key_edit = QLineEdit()
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.reveal_button = QPushButton("显示")
        self.reveal_button.setAccessibleName("显示密钥")
        self.reveal_button.clicked.connect(self._toggle_reveal)
        self.key_row = QWidget()
        key_line = QHBoxLayout(self.key_row)
        key_line.setContentsMargins(0, 0, 0, 0)
        key_line.addWidget(self.key_edit, 1)
        key_line.addWidget(self.reveal_button)
        self.key_label = QLabel("API 密钥")
        form.addRow("服务商", self.preset_combo)
        form.addRow("名称", self.name_edit)
        form.addRow("接口地址", self.url_edit)
        form.addRow("模型", self.model_edit)
        form.addRow(self.key_label, self.key_row)
        layout.addLayout(form)
        self.result_label = _label("", "vocabQuiet")
        self.result_label.setWordWrap(True)
        layout.addWidget(self.result_label)
        buttons = QHBoxLayout()
        self.test_button = QPushButton("测试连接")
        self.test_button.clicked.connect(self._run_test)
        buttons.addWidget(self.test_button)
        buttons.addStretch()
        if entry:
            delete = QPushButton("删除")
            delete.clicked.connect(self._delete)
            buttons.addWidget(delete)
        self.save_button = QPushButton("保存")
        self.save_button.setDefault(True)
        self.save_button.clicked.connect(self._save)
        buttons.addWidget(self.save_button)
        layout.addLayout(buttons)

        self._filled = {}
        if entry:
            self.preset_combo.setCurrentIndex(max(0, self.preset_combo.findData(entry.get("preset"))))
            self.name_edit.setText(entry.get("name", ""))
            self.url_edit.setText(entry.get("base_url", ""))
            self.model_edit.setText(entry.get("model", ""))
            self.key_edit.setText(entry.get("api_key", ""))
            self.key_edit.setPlaceholderText(masked(entry.get("api_key", "")))
            self._filled = {"name": entry.get("name", ""), "base_url": entry.get("base_url", ""),
                            "model": entry.get("model", "")}
        else:
            self.preset_combo.setCurrentIndex(0)
            self._apply_preset()
        self.preset_combo.currentIndexChanged.connect(self._apply_preset)
        self._update_key_row()

    def _apply_preset(self, *_index) -> None:
        preset = PRESETS[self.preset_combo.currentData()]
        for field, edit, value in (("name", self.name_edit, preset.label),
                                   ("base_url", self.url_edit, preset.base_url),
                                   ("model", self.model_edit, preset.model)):
            if edit.text() == self._filled.get(field, ""):      # 用户改过的不覆盖
                edit.setText(value)
                self._filled[field] = value
        self._update_key_row()

    def _update_key_row(self) -> None:
        """只有 Ollama 本机完全不用密钥;自定义服务密钥可选, 仍给出填写框。"""
        hide = self.preset_combo.currentData() == "ollama"
        self.key_row.setHidden(hide)
        self.key_label.setHidden(hide)
        self.key_label.setText("API 密钥" if PRESETS[self.preset_combo.currentData()].needs_key
                               else "API 密钥（可选）")

    def _toggle_reveal(self) -> None:
        hidden = self.key_edit.echoMode() == QLineEdit.EchoMode.Password
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Normal if hidden else QLineEdit.EchoMode.Password)
        self.reveal_button.setText("隐藏" if hidden else "显示")

    def values(self) -> dict:
        out = {"preset": self.preset_combo.currentData(), "name": self.name_edit.text().strip(),
               "base_url": self.url_edit.text().strip(), "model": self.model_edit.text().strip(),
               "api_key": self.key_edit.text().strip() if not self.key_row.isHidden() else ""}
        if self.entry:
            out["id"] = self.entry["id"]
        return out

    def _run_test(self) -> None:
        self.result_label.setText("正在测试…")
        self.test_button.setEnabled(False)
        self._test(self.values(), self._show_result)

    def _save(self) -> None:
        missing = missing_field(self.values())
        if missing:
            self.result_label.setText(FAILURE_HINT[missing])
            return
        self.accept()

    def _show_result(self, status) -> None:
        self.test_button.setEnabled(True)
        if status.kind == "ok" and status.slow:
            self.result_label.setText(SLOW_HINT.format(s="%.1f" % ((status.ms or 0) / 1000)))
        elif status.kind == "ok":
            self.result_label.setText(status_text(status))
        else:
            self.result_label.setText(FAILURE_HINT.get(status.kind, FAILURE_HINT["bad_reply"]))

    def _delete(self) -> None:
        self.deleted = True
        self.accept()


class ServiceList(QWidget):
    def __init__(self, services: Callable[[], list], *, save: Callable, delete: Callable,
                 reorder: Callable[[list], None], test: Callable, status: Callable,
                 unreadable: Callable[[], bool] = lambda: False):
        super().__init__()
        self._services, self._save, self._delete = services, save, delete
        self._reorder, self._test, self._status, self._unreadable = reorder, test, status, unreadable
        self.row_widgets: list = []
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 8)
        outer.setSpacing(4)
        hint = _label(LIST_HINT, "vocabQuiet")
        hint.setWordWrap(True)
        outer.addWidget(hint)
        self.warning = _label(UNREADABLE_TEXT, "vocabQuiet")
        self.warning.setWordWrap(True)
        outer.addWidget(self.warning)
        self.box = QFrame(objectName="mainInset")
        self.rows = QVBoxLayout(self.box)
        self.rows.setContentsMargins(8, 4, 8, 4)
        self.rows.setSpacing(0)
        outer.addWidget(self.box)
        add = QPushButton("添加服务")
        add.clicked.connect(self._add)
        add_line = QHBoxLayout()
        add_line.addWidget(add)
        add_line.addStretch()
        outer.addLayout(add_line)
        outer.addWidget(_label(STORAGE_NOTE, "vocabQuiet"))
        self.refresh()

    def refresh(self) -> None:
        self.warning.setHidden(not self._unreadable())
        self.row_widgets = []
        while self.rows.count():            # 旧行留在原父级下隐藏并延后删除, 不变成无主的顶层窗口
            widget = self.rows.takeAt(0).widget()
            if widget is not None:
                widget.hide()
                widget.deleteLater()
        entries = self._services()
        if not entries:
            empty = _label(EMPTY_TEXT, "vocabQuiet")
            empty.setWordWrap(True)
            self.rows.addWidget(empty)
            return
        ids = [e["id"] for e in entries]
        for index, e in enumerate(entries):
            row = QWidget(objectName="serviceRow")
            line = QHBoxLayout(row)
            line.setContentsMargins(0, 4, 0, 4)
            line.addWidget(_label(str(index + 1), "vocabQuiet"))
            words = QVBoxLayout()
            words.setSpacing(0)
            words.addWidget(_label(e.get("name", ""), "settingLabel"))
            words.addWidget(_label(e.get("model", ""), "vocabQuiet"))
            line.addLayout(words, 1)
            line.addWidget(_label(status_text(self._status(e["id"])), "vocabQuiet"))
            for name, text, delta in (("moveUp", "上移", -1), ("moveDown", "下移", 1)):
                button = QPushButton(text, objectName=name)
                target = index + delta
                button.setEnabled(0 <= target < len(ids))
                button.setProperty("swap", (index, target))
                button.clicked.connect(self._on_move)
                line.addWidget(button)
            edit = QPushButton("编辑", objectName="edit")
            edit.setProperty("entry_id", e["id"])
            edit.clicked.connect(self._on_edit)
            line.addWidget(edit)
            self.rows.addWidget(row)
            self.row_widgets.append(row)

    # 槽函数用绑定方法 + 按钮属性取参数: 连到引用 self 的 lambda 会形成循环引用, 控件不能及时释放
    def _add(self) -> None:
        self._edit(None)

    def _on_edit(self) -> None:
        entry_id = self.sender().property("entry_id")
        entry = next((e for e in self._services() if e["id"] == entry_id), None)
        if entry is not None:
            self._edit(entry)

    def _on_move(self) -> None:
        index, target = self.sender().property("swap")
        self._move([e["id"] for e in self._services()], index, target)

    def _move(self, ids: list, index: int, target: int) -> None:
        if not 0 <= target < len(ids):
            return
        order = list(ids)
        order[index], order[target] = order[target], order[index]
        self._reorder(order)
        self.refresh()

    def _edit(self, entry: Optional[dict]) -> None:
        dialog = ServiceDialog(entry, self._test, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            if dialog.deleted:
                self._delete(entry["id"])
            else:
                self._save(entry["id"] if entry else None, dialog.values())
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "整理服务", "没有保存：%s" % type(exc).__name__)
        self.refresh()
