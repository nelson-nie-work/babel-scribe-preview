"""Scribe 主窗口与词库页。关闭窗口即结束词库的撤销会话。"""
from json import JSONDecodeError
import re

from PySide6.QtCore import QSize, QSignalBlocker, Qt
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QPushButton, QStackedWidget, QVBoxLayout, QWidget,
)

from scribe import vocab
from scribe.config import APP_NAME
from scribe.learn_dialog import reveal_learning_dialog as reveal_window
from scribe.tray import make_icon
from scribe.vocab_store import VocabularyStore


_SOURCE_NAMES = {"manual": "手动", "learned": "学习", "folder": "文件夹"}
_STYLE = """
QWidget#vocabWindow, QDialog#vocabConfirm { background: #f5f5f7; color: #1d1d1f; }
QWidget { font-family: 'Microsoft YaHei UI', 'Segoe UI'; font-size: 13px; color: #1d1d1f; }
QFrame#vocabSidebar { background: #ececf0; border-right: 1px solid #dedee4; }
QFrame#vocabBrowser { background: #fff; border-right: 1px solid #dedee4; }
QFrame#vocabFooter { border-top: 1px solid #dedee4; }
QFrame#vocabFields { background: #fff; border: 1px solid #dedee4; border-radius: 9px; }
QFrame#vocabDivider { color: #ececf0; }
QLabel { background: transparent; }
QLabel#vocabHeading { font-size: 20px; font-weight: 600; }
QLabel#vocabTitle { font-size: 17px; font-weight: 600; }
QLabel#vocabBrand, QLabel#vocabTerm { font-weight: 600; }
QLabel#vocabQuiet { color: #65656e; font-size: 12px; }
QLabel#vocabActiveNav { color: #fff; background: #0069d4; border-radius: 6px; padding: 5px 9px; }
QLabel#vocabInactiveNav { color: #65656e; padding: 5px 9px; }
QLabel#vocabError { color: #a6252b; font-size: 12px; }
QPushButton#mainNav { text-align: left; background: transparent; border: 1px solid transparent; padding: 0 9px; }
QPushButton#mainNav:hover { background: #e2e2e8; }
QPushButton#mainNav:checked { color: white; background: #0069d4; }
QPushButton#mainNav:focus { border-color: #7aaff0; }
QPushButton#historyFilter, QPushButton#historyDisclosure { border-color: transparent; background: transparent; }
QPushButton#historyFilter:checked, QPushButton#historyFilter:hover { background: #e9f2ff; color: #0055a9; }
QPushButton#historyDisclosure { text-align: left; padding: 0 3px; }
QPushButton#historyFilter:focus, QPushButton#historyDisclosure:focus { border-color: #7aaff0; }
QPushButton#historyMore { font-size: 16px; padding: 0; }
QPushButton#historyMore::menu-indicator { image: none; width: 0; }
QMenu#historyMoreMenu { background: white; border: 1px solid #dedee4; padding: 4px; }
QMenu#historyMoreMenu::item { padding: 6px 10px; border-radius: 4px; }
QMenu#historyMoreMenu::item:selected { background: #e9f2ff; color: #1d1d1f; }
QFrame#mainInset { background: #fff; border: 1px solid #dedee4; border-radius: 9px; }
QLabel#mainSection { font-weight: 600; }
QLabel#settingLabel { color: #26262b; font-weight: 400; }
QLabel#shortcutBadge { background: #f5f5f7; border: 1px solid #dedee4; border-radius: 5px; padding: 3px 7px; }
QLabel#historyText { font-size: 16px; }
QScrollArea#mainScroll { background: transparent; border: none; }
QWidget#mainScrollContent { background: #f5f5f7; }
QComboBox#settingCombo { background: white; border: 1px solid #d0d0d7; border-radius: 6px; padding: 4px 24px 4px 7px; }
QComboBox#settingCombo:focus { border-color: #0069d4; }
QComboBox#settingCombo:disabled { color: #767681; background: #eeeef1; }
QComboBox#settingCombo::drop-down { subcontrol-origin: padding; subcontrol-position: top right; width: 22px; border: none; background: transparent; }
QComboBox#settingCombo::down-arrow { image: none; }
QLineEdit { min-height: 29px; background: #fff; border: 1px solid #d0d0d7; border-radius: 6px; padding: 0 8px; selection-background-color: #0069d4; }
QLineEdit#vocabSearch { background: #f5f5f7; }
QLineEdit:focus { border-color: #0069d4; }
QListWidget { border: none; background: #fff; outline: none; }
QListWidget::item { border-radius: 6px; }
QListWidget::item:hover { background: #f5f5f7; }
QListWidget::item:selected { background: #e9f2ff; color: #1d1d1f; }
QListWidget::item:selected:focus { border: 1px solid #82b9f4; }
QPushButton { min-height: 27px; background: #fff; border: 1px solid #d2d2d8; border-radius: 6px; padding: 0 10px; }
QPushButton:hover { background: #f5f5f7; }
QPushButton:focus { border-color: #0069d4; }
QPushButton#vocabSave { background: #0069d4; border-color: #0069d4; color: #fff; }
QPushButton#vocabSave:hover { background: #005ebf; }
QPushButton#vocabDelete { color: #a6252b; }
QPushButton#vocabUndo { color: #0062c4; background: transparent; border-color: transparent; }
QPushButton#vocabUndo:focus { border-color: #0069d4; }
QPushButton:disabled, QPushButton#vocabSave:disabled { background: #eeeef1; border-color: #dedee5; color: #777781; }
"""


def _label(text="", name=""):
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setObjectName(name)
    return label


def _source(entry: dict) -> str:
    return _SOURCE_NAMES.get(entry.get("source"), "原有")


class VocabularyWindow(QWidget):
    def __init__(self, store: VocabularyStore, *, pages: dict[str, QWidget] | None = None):
        super().__init__()
        self.store = store
        self._entry: dict | None = None
        self._adding = False
        self._deleted: tuple[int, dict] | None = None
        self._initial = ()
        self._pages = pages or {}
        self._nav = {}
        self.page = "vocab"
        self.setObjectName("vocabWindow")
        self.setWindowTitle(f"{APP_NAME} · 词库")
        self.setWindowIcon(make_icon())
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setStyleSheet(_STYLE)
        self.setMinimumSize(745, 440)
        self.resize(1000, 640) if self._pages else self.resize(920, 508)

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._sidebar())
        self._stack = QStackedWidget()
        root.addWidget(self._stack, 1)
        word_page = QWidget()
        grid = QGridLayout(word_page)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(0)
        grid.addWidget(self._browser(), 0, 0)
        grid.addWidget(self._detail(), 0, 1)
        grid.addWidget(self._footer(), 1, 0, 1, 2)
        grid.setColumnStretch(0, 36)
        grid.setColumnStretch(1, 64)
        grid.setRowStretch(0, 1)
        self._panels = {"vocab": word_page, **self._pages}
        for panel in self._panels.values():
            self._stack.addWidget(panel)
        self._select(self._first_entry())
        self.set_page("general" if self._pages else "vocab")

    def _sidebar(self):
        panel = QFrame(objectName="vocabSidebar")
        panel.setFixedWidth(148)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(10, 17, 10, 12)
        layout.setSpacing(3)
        brand = QHBoxLayout()
        icon = _label()
        icon.setPixmap(self.windowIcon().pixmap(23, 23))
        brand.addWidget(icon)
        brand.addWidget(_label("Scribe", "vocabBrand"))
        brand.addStretch()
        layout.addLayout(brand)
        layout.addSpacing(12)
        for page, title in (("general", "通用"), ("vocab", "词库"), ("history", "历史")):
            if not self._pages:
                layout.addWidget(_label(title, "vocabActiveNav" if page == "vocab" else "vocabInactiveNav"))
            elif page == "vocab" or page in self._pages:
                button = QPushButton(title, objectName="mainNav", checkable=True)
                button.clicked.connect(lambda _checked=False, name=page: self.set_page(name))
                self._nav[page] = button
                layout.addWidget(button)
        layout.addStretch()
        return panel

    def set_page(self, page: str) -> None:
        panel = self._panels[page]
        self.page = page
        self._stack.setCurrentWidget(panel)
        for name, button in self._nav.items():
            button.setChecked(page == name)
        title = {"general": "设置", "vocab": "词库", "history": "听写历史"}[page]
        self.setWindowTitle(f"{APP_NAME} · {title}")
        if page == "vocab":
            self._reload_words()
        elif hasattr(panel, "refresh"):
            panel.refresh()

    def _browser(self):
        panel = QFrame(objectName="vocabBrowser")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 17, 12, 10)
        layout.setSpacing(8)
        header = QHBoxLayout()
        header.addWidget(_label("词库", "vocabHeading"))
        header.addStretch()
        self.add_button = QPushButton("添加")
        self.add_button.clicked.connect(self._add)
        header.addWidget(self.add_button)
        layout.addLayout(header)
        self.search = QLineEdit(objectName="vocabSearch")
        self.search.setPlaceholderText("搜索词或错写")
        self.search.setAccessibleName("搜索词或错写")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._render_list)
        layout.addWidget(self.search)
        self.count = _label("", "vocabQuiet")
        layout.addWidget(self.count)
        self.list = QListWidget()
        self.list.setAccessibleName("词条列表")
        self.list.currentItemChanged.connect(self._choose)
        layout.addWidget(self.list, 1)
        return panel

    def _detail(self):
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(25, 23, 25, 16)
        layout.setSpacing(11)
        header = QHBoxLayout()
        self.heading = _label("", "vocabTitle")
        self.heading.setWordWrap(True)
        self.source = _label("", "vocabQuiet")
        header.addWidget(self.heading, 1)
        header.addWidget(self.source)
        layout.addLayout(header)
        self.empty = _label("添加一个你希望 Scribe 记住的词。", "vocabQuiet")
        layout.addWidget(self.empty)
        self.editor = QWidget()
        body = QVBoxLayout(self.editor)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(9)
        box = QFrame(objectName="vocabFields")
        fields = QGridLayout(box)
        fields.setContentsMargins(12, 8, 12, 10)
        fields.setVerticalSpacing(7)
        self.term = QLineEdit()
        self.aliases = QLineEdit()
        self.aliases.setPlaceholderText("多个错写用逗号分隔")
        self.note = QLineEdit()
        self.note.setPlaceholderText("选填")
        for row, (title, field) in enumerate((
            ("正确写法", self.term), ("常被听成", self.aliases), ("备注", self.note),
        )):
            fields.addWidget(_label(title), row * 2, 0)
            field.setAccessibleName(title)
            field.textChanged.connect(self._edited)
            field.returnPressed.connect(self._save)
            fields.addWidget(field, row * 2, 1)
            line = QFrame(objectName="vocabDivider")
            line.setFrameShape(QFrame.Shape.HLine)
            fields.addWidget(line, row * 2 + 1, 0, 1, 2)
        fields.setColumnStretch(1, 1)
        fields.addWidget(_label("同音自动纠正"), 6, 0, 1, 2)
        self.pinyin = QCheckBox()
        self.pinyin.setAccessibleName("同音自动纠正")
        self.pinyin.toggled.connect(self._edited)
        fields.addWidget(self.pinyin, 6, 1, Qt.AlignmentFlag.AlignRight)
        self.pinyin_hint = _label("", "vocabQuiet")
        self.pinyin_hint.setWordWrap(True)
        fields.addWidget(self.pinyin_hint, 7, 0, 1, 2)
        body.addWidget(box)
        self.error = _label("", "vocabError")
        self.error.setWordWrap(True)
        body.addWidget(self.error)
        actions = QHBoxLayout()
        self.delete_button = QPushButton("删除词条", objectName="vocabDelete")
        self.delete_button.clicked.connect(self._delete)
        self.cancel_button = QPushButton("取消")
        self.cancel_button.clicked.connect(self._cancel)
        self.save_button = QPushButton("保存", objectName="vocabSave")
        self.save_button.clicked.connect(self._save)
        actions.addWidget(self.delete_button)
        actions.addStretch()
        actions.addWidget(self.cancel_button)
        actions.addWidget(self.save_button)
        body.addLayout(actions)
        layout.addWidget(self.editor)
        layout.addStretch()
        return panel

    def _footer(self):
        panel = QFrame(objectName="vocabFooter")
        layout = QHBoxLayout(panel)
        layout.setContentsMargins(13, 5, 13, 5)
        self.status = _label("选中正确写法，按右 Ctrl + L 可直接收词。", "vocabQuiet")
        self.undo_button = QPushButton("撤销", objectName="vocabUndo")
        self.undo_button.clicked.connect(self._undo)
        self.undo_button.hide()
        layout.addWidget(self.status)
        layout.addWidget(self.undo_button)
        layout.addStretch()
        return panel

    def _reload_words(self) -> None:
        try:
            self.store.reload()
            self.refresh_from_store()
        except (OSError, JSONDecodeError, TypeError, KeyError, AttributeError):
            self.status.setText("无法读取词库文件，请检查后再试。")

    def open_from_tray(self) -> bool:
        self.set_page(self.page)
        if self.isMinimized():
            self.showNormal()
        return reveal_window(self)

    def refresh_from_store(self) -> None:
        if self._adding or self._dirty():
            self._render_list()
            return
        key = vocab.spelling_key(self._entry["term"]) if self._entry else ""
        current = next((entry for entry in self.store.current.terms
                        if vocab.spelling_key(entry["term"]) == key), None)
        self._select(current or self._first_entry())

    def _ordered_entries(self):
        return sorted(self.store.current.terms,
                      key=lambda entry: (str(entry.get("created") or ""), str(entry.get("created_at") or "")),
                      reverse=True)

    def _first_entry(self):
        return next(iter(self._ordered_entries()), None)

    def _render_list(self):
        query = vocab.spelling_key(self.search.text())
        matches = [entry for entry in self._ordered_entries()
                   if any(query in vocab.spelling_key(spelling)
                          for spelling in (entry["term"], *entry.get("aliases", ())))]
        with QSignalBlocker(self.list):
            self.list.clear()
            for entry in matches:
                item = QListWidgetItem(self.list)
                item.setData(Qt.ItemDataRole.UserRole, entry)
                item.setData(Qt.ItemDataRole.AccessibleTextRole, entry["term"])
                item.setSizeHint(QSize(0, 42))
                row = QWidget()
                row.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
                line = QHBoxLayout(row)
                line.setContentsMargins(8, 0, 8, 0)
                term = _label(entry["term"], "vocabTerm")
                term.setWordWrap(True)
                line.addWidget(term, 1)
                line.addWidget(_label(_source(entry), "vocabQuiet"))
                self.list.setItemWidget(item, row)
                if entry == self._entry:
                    self.list.setCurrentItem(item)
        count = len(matches)
        self.count.setText(f"{count} 项匹配" if query else f"最近加入在前 · {count} 个词")
        if not matches:
            self.count.setText("没有匹配的词条" if query else "词库还是空的")

    def _select(self, entry, *, adding=False, update_list=True):
        self._entry = entry
        self._adding = adding
        self.editor.setVisible(entry is not None or adding)
        self.empty.setVisible(entry is None and not adding)
        self.heading.setText(entry["term"] if entry else "添加词条" if adding else "词条详情")
        self.source.setText(_source(entry) if entry else "")
        values = entry or {}
        with QSignalBlocker(self.term), QSignalBlocker(self.aliases), QSignalBlocker(self.note), QSignalBlocker(self.pinyin):
            self.term.setText(values.get("term", ""))
            self.aliases.setText("，".join(values.get("aliases", ())))
            self.note.setText(values.get("note", ""))
            self.pinyin.setChecked(values.get("pinyin") is True)
        self._initial = self._values()
        self.delete_button.setVisible(entry is not None)
        self._edited()
        if update_list:
            self._render_list()

    def _values(self):
        return self.term.text(), self.aliases.text(), self.note.text(), self.pinyin.isChecked()

    def _dirty(self) -> bool:
        return not self.editor.isHidden() and self._values() != self._initial

    def _edited(self):
        term = self.term.text().strip()
        others = (row["term"] for row in self.store.current.terms if row != self._entry)
        allowed = vocab.can_match_pinyin(term, others)
        with QSignalBlocker(self.pinyin):
            if not allowed and term != (self._entry or {}).get("term"):
                self.pinyin.setChecked(False)
        self.pinyin.setEnabled(allowed)
        self.pinyin_hint.setText("仅建议为人名、项目名等专有词开启。" if allowed
                                 else "当前写法请用上方的固定错写纠正。")
        self.save_button.setEnabled(self._dirty())
        self.cancel_button.setVisible(self._adding or self._dirty())
        self.error.hide()

    def _choose(self, current, _previous):
        if current is None:
            return
        entry = current.data(Qt.ItemDataRole.UserRole)
        if entry == self._entry:
            return
        if self._can_leave():
            self._select(entry, update_list=False)
        else:
            self._render_list()

    def _add(self):
        if self._can_leave():
            self._select(None, adding=True)
            self.term.setFocus()

    def _cancel(self):
        self._select(self._entry or self._first_entry())

    def _save(self):
        if not self._dirty():
            return
        term, aliases, note, pinyin = self._values()
        spellings = (list(self._entry.get("aliases", ())) if self._entry and aliases == self._initial[1]
                     else re.split(r"[,，、]", aliases))
        try:
            entry = self.store.save_entry(self._entry, term=term, aliases=spellings,
                                          note=note, pinyin=pinyin)
        except (OSError, JSONDecodeError, TypeError, KeyError, AttributeError):
            self._show_error("词库保存失败，请检查词库文件。")
            return
        except ValueError as exc:
            self._show_error(str(exc))
            return
        if self._adding:
            with QSignalBlocker(self.search):
                self.search.clear()
        self._select(entry)
        self.status.setText(f"已保存「{entry['term']}」")

    def _delete(self):
        entry = self._entry
        if entry is None or not self._confirm(f"删除「{entry['term']}」？", "删除后可在词库页撤销。", "删除"):
            return
        try:
            self._deleted = self.store.delete_entry(entry)
        except (OSError, JSONDecodeError, TypeError, KeyError, AttributeError):
            self._show_error("词库删除失败，请检查词库文件。")
            return
        except ValueError as exc:
            self._show_error(str(exc))
            return
        self._select(self._first_entry())
        self.status.setText(f"已删除「{entry['term']}」")
        self.undo_button.setAccessibleName(f"撤销删除 {entry['term']}")
        self.undo_button.show()
        self.undo_button.setFocus()

    def _undo(self):
        if self._deleted is None or not self._can_leave():
            return
        try:
            entry = self.store.restore_entry(self._deleted)
        except (OSError, JSONDecodeError, TypeError, KeyError, AttributeError):
            self.status.setText("撤销未保存，请检查词库文件。")
            return
        except ValueError as exc:
            self.status.setText(str(exc))
            return
        self._deleted = None
        self.undo_button.hide()
        with QSignalBlocker(self.search):
            self.search.clear()
        self._select(entry)
        self.status.setText(f"已恢复「{entry['term']}」")

    def _show_error(self, message):
        self.error.setText(message)
        self.error.show()

    def _can_leave(self):
        return not self._dirty() or self._confirm("放弃这次修改？", "词库会保留上一次保存的写法。", "放弃修改")

    def _confirm(self, title, detail, action):
        dialog = QDialog(self, Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        dialog.setObjectName("vocabConfirm")
        dialog.setMinimumWidth(315)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(17, 15, 17, 14)
        layout.addWidget(_label(title, "vocabTitle"))
        layout.addWidget(_label(detail, "vocabQuiet"))
        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel = QPushButton("取消")
        cancel.setDefault(True)
        cancel.clicked.connect(dialog.reject)
        proceed = QPushButton(action, objectName="vocabDelete")
        proceed.setAutoDefault(False)
        proceed.clicked.connect(dialog.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(proceed)
        layout.addLayout(buttons)
        result = dialog.exec() == QDialog.DialogCode.Accepted
        dialog.deleteLater()
        return result

    def closeEvent(self, event):
        if self._can_leave():
            event.accept()
        else:
            event.ignore()
