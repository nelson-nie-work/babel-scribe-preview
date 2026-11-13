"""听写历史的列表、原文对照和逐应用输出规则。"""
import sqlite3
from collections.abc import Callable
from datetime import date, datetime, timedelta

from PySide6.QtCore import QSignalBlocker, QSize, Qt, QTimer
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QLineEdit, QListWidget, QListWidgetItem, QMenu, QPushButton,
    QScrollArea, QVBoxLayout, QWidget,
)

from scribe.history import HistoryStore, Record
from scribe.learn_dialog import confirm_alias
from scribe.learning import valid_word
from scribe.vocab_window import _label


def _day(when: datetime) -> str:
    today = date.today()
    if when.date() == today:
        return "今天"
    if when.date() == today - timedelta(days=1):
        return "昨天"
    return when.strftime("%m 月 %d 日")


def _display_time(record: Record) -> tuple[str, str]:
    local = datetime.fromtimestamp(record.created_at)
    return _day(local), local.strftime("%H:%M")


class HistoryPage(QWidget):
    def __init__(self, store: HistoryStore, *, copy: Callable[[str], None],
                 learn: Callable[[str, str], bool], typing: Callable[[str], bool],
                 set_typing: Callable[[str, bool], None]):
        super().__init__()
        self.store, self.copy, self.learn = store, copy, learn
        self.typing, self.set_typing = typing, set_typing
        self._records = {}
        self.record = None
        self.filter = "all"
        self._filters = {}
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._browser(), 42)
        layout.addWidget(self._details(), 58)

    def _browser(self):
        panel = QFrame(objectName="vocabBrowser")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(14, 18, 13, 13)
        layout.setSpacing(8)
        layout.addWidget(_label("听写历史", "vocabHeading"))
        layout.addWidget(_label("最近 30 天，仅存于这台电脑。", "vocabQuiet"))
        self.search = QLineEdit(objectName="vocabSearch")
        self.search.setPlaceholderText("搜索文字或应用")
        self.search.setAccessibleName("搜索文字或应用")
        self.search.setClearButtonEnabled(True)
        self._search_timer = QTimer(self, interval=120, singleShot=True, timeout=self.refresh)
        self.search.textChanged.connect(lambda _: self._search_timer.start())
        layout.addWidget(self.search)
        controls = QHBoxLayout()
        controls.setSpacing(4)
        for value, title in (("all", "全部"), ("raw", "直出"), ("smart", "已整理")):
            button = QPushButton(title, objectName="historyFilter", checkable=True)
            button.setChecked(value == self.filter)
            button.clicked.connect(lambda _checked=False, route=value: self._set_filter(route))
            self._filters[value] = button
            controls.addWidget(button)
        controls.addStretch()
        layout.addLayout(controls)
        self.list = QListWidget()
        self.list.setAccessibleName("听写列表")
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.currentItemChanged.connect(self._select)
        layout.addWidget(self.list, 1)
        self.empty = _label("", "vocabQuiet")
        self.empty.setWordWrap(True)
        layout.addWidget(self.empty)
        return panel

    def _details(self):
        scroll = QScrollArea(objectName="mainScroll", widgetResizable=True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget(objectName="mainScrollContent")
        scroll.setWidget(body)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(21, 18, 21, 13)
        layout.setSpacing(10)
        self.placeholder = _label("选中一条听写，查看完整文字。", "vocabQuiet")
        layout.addWidget(self.placeholder)
        self.detail = QWidget()
        detail = QVBoxLayout(self.detail)
        detail.setContentsMargins(0, 0, 0, 0)
        detail.setSpacing(9)
        heading = QHBoxLayout()
        self.app_name = _label("", "vocabTitle")
        self.app_name.setWordWrap(True)
        heading.addWidget(self.app_name, 1)
        copy = QPushButton("复制", objectName="vocabSave")
        copy.clicked.connect(self._copy)
        heading.addWidget(copy)
        self.more = QPushButton("…", objectName="historyMore")
        self.more.setAccessibleName("更多操作")
        self.more.setToolTip("更多操作")
        self.more.setFixedWidth(34)
        menu = QMenu(self.more, objectName="historyMoreMenu")
        self.rule_action = menu.addAction("")
        self.rule_action.triggered.connect(self._confirm_rule)
        self.more.setMenu(menu)
        heading.addWidget(self.more)
        detail.addLayout(heading)
        self.meta = _label("", "vocabQuiet")
        self.meta.setWordWrap(True)
        detail.addWidget(self.meta)
        self.rule_state = _label("", "vocabQuiet")
        self.rule_state.setWordWrap(True)
        self.rule_state.hide()
        detail.addWidget(self.rule_state)

        paper = QFrame(objectName="mainInset")
        lines = QVBoxLayout(paper)
        lines.setContentsMargins(13, 10, 13, 11)
        lines.addWidget(_label("最终文字", "vocabQuiet"))
        self.text = _label("", "historyText")
        self.text.setWordWrap(True)
        self.text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lines.addWidget(self.text)
        detail.addWidget(paper)
        learn = QPushButton("改正并学习")
        learn.clicked.connect(self._learn)
        detail.addWidget(learn, 0, Qt.AlignmentFlag.AlignLeft)

        compare = QFrame(objectName="mainInset")
        compare_layout = QVBoxLayout(compare)
        compare_layout.setContentsMargins(10, 5, 10, 6)
        self.toggle = QPushButton("▸  查看原始识别", objectName="historyDisclosure", checkable=True)
        self.toggle.toggled.connect(self._expand)
        compare_layout.addWidget(self.toggle)
        self.raw = QWidget()
        raw_layout = QHBoxLayout(self.raw)
        raw_layout.setContentsMargins(3, 1, 3, 5)
        raw_layout.setSpacing(12)
        self.sensevoice = self._original(raw_layout, "SenseVoice")
        self.whisper = self._original(raw_layout, "Whisper")
        compare_layout.addWidget(self.raw)
        self.raw.hide()
        detail.addWidget(compare)

        layout.addWidget(self.detail)
        self.detail.hide()
        layout.addStretch()
        self.status = _label("", "vocabQuiet")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        return scroll

    @staticmethod
    def _original(layout, name):
        column = QVBoxLayout()
        column.addWidget(_label(name, "mainSection"))
        text = _label("", "vocabQuiet")
        text.setWordWrap(True)
        text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        column.addWidget(text)
        column.addStretch()
        layout.addLayout(column, 1)
        return text

    def refresh(self) -> None:
        previous = self.record.id if self.record else None
        try:
            records = self.store.list(self.search.text(), self.filter)
        except sqlite3.Error:
            records = []
            message = "暂时无法读取历史，请重新打开这页。"
        else:
            message = ("没有匹配的听写，换个关键词试试。"
                       if self.search.text().strip() or self.filter != "all"
                       else "完成一次听写后，文字会出现在这里。")
        self._records = {record.id: record for record in records}
        first = selected = None
        last_day = None
        with QSignalBlocker(self.list):
            self.list.clear()
            for record in records:
                day, when = _display_time(record)
                if day != last_day:
                    group = QListWidgetItem(day, self.list)
                    group.setFlags(Qt.ItemFlag.NoItemFlags)
                    group.setForeground(QBrush(QColor("#65656e")))
                    last_day = day
                excerpt = " ".join(record.text.split())
                item = QListWidgetItem(f"{when}  {record.app_name}     {record.route}\n{excerpt}", self.list)
                item.setSizeHint(QSize(0, 43))
                item.setData(Qt.ItemDataRole.UserRole, record.id)
                first = first or item
                if record.id == previous:
                    selected = item
            self.list.setCurrentItem(selected or first)
        self.empty.setText(message)
        self.empty.setVisible(not records)
        self.list.setVisible(bool(records))
        self._select(selected or first)

    def _set_filter(self, route: str) -> None:
        self.filter = route
        for name, button in self._filters.items():
            button.setChecked(name == route)
        self.refresh()

    def _select(self, item, _previous=None) -> None:
        record = self._records.get(item.data(Qt.ItemDataRole.UserRole)) if item else None
        if record != self.record:
            self.status.clear()
            self.toggle.setChecked(False)
        self.record = record
        self.detail.setVisible(record is not None)
        self.placeholder.setVisible(record is None)
        if record is None:
            return
        day, when = _display_time(record)
        suffix = {"copied_only": "  ·  仅复制", "failed": "  ·  未输入",
                  "clipboard_protected": "  ·  未输入"}.get(record.outcome, "")
        self.app_name.setText(record.app_name)
        self.meta.setText(f"{day} {when}    松手后 {record.elapsed_ms / 1000:.2f} 秒    {record.route}{suffix}")
        self.text.setText(record.text)
        self.sensevoice.setText(record.sensevoice or "没有识别结果")
        self.whisper.setText(record.whisper or "没有识别结果")
        self._render_rule()

    def _expand(self, opened: bool) -> None:
        self.toggle.setText(("▾" if opened else "▸") + "  查看原始识别")
        self.raw.setVisible(opened)

    def _copy(self) -> None:
        try:
            self.copy(self.record.text)
        except OSError:
            self.status.setText("复制失败，可选中文字后按 Ctrl+C。")
        else:
            self.status.setText("已复制最终文字。")

    def _learn(self) -> None:
        selected = self.text.selectedText().strip()
        pair = confirm_alias(selected if valid_word(selected) else "", "", original=self.record.text, history=True)
        if pair is None:
            return
        try:
            added = self.learn(*pair)
        except (OSError, TypeError, KeyError, ValueError) as exc:
            self.status.setText(str(exc) if isinstance(exc, ValueError) else "词库未保存，请重试。")
        else:
            self.status.setText(f"已加入词库「{pair[1]}」。原听写记录保留。" if added else "这个写法已在词库中。")

    def _render_rule(self) -> None:
        app = self.record.app_id
        active = bool(app) and self.typing(app)
        name = self.record.app_name
        self.more.setVisible(bool(app))
        self.rule_action.setText("恢复默认粘贴" if active else f"在「{name}」中改用逐字输入")
        self.rule_state.setText(f"{name}使用逐字输入" if active else "")
        self.rule_state.setVisible(active)

    def _confirm_rule(self) -> None:
        record = self.record
        if not record or not record.app_id:
            return
        enable = not self.typing(record.app_id)
        title = f"在「{record.app_name}」中逐字输入？" if enable else "恢复默认粘贴？"
        action = "改用逐字输入" if enable else "恢复默认粘贴"
        detail = ("下次在这个应用里听写时，会直接逐字输入。你可以在同一个菜单里恢复默认粘贴。"
                  if enable else f"下次在「{record.app_name}」中听写，会用粘贴输出。")
        dialog = QDialog(self, Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        dialog.setObjectName("vocabConfirm")
        dialog.setFixedWidth(370)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(18, 16, 18, 15)
        layout.addWidget(_label(title, "vocabTitle"))
        label = _label(detail, "vocabQuiet")
        label.setWordWrap(True)
        layout.addWidget(label)
        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel = QPushButton("取消")
        cancel.clicked.connect(dialog.reject)
        proceed = QPushButton(action, objectName="vocabSave", autoDefault=False)
        proceed.clicked.connect(dialog.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(proceed)
        layout.addLayout(buttons)
        cancel.setFocus()
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        dialog.deleteLater()
        if not accepted:
            return
        try:
            self.set_typing(record.app_id, enable)
        except OSError:
            self.status.setText("输出方式未保存，请重试。")
        else:
            self.status.clear()
        if self.record:
            self._render_rule()
