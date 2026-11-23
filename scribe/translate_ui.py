"""划词翻译的三个小窗口：悬浮条、译文卡片、轻提示。

都不抢焦点（选区所在窗口保持输入焦点），写法同 word_capsule.py；
卡片和提示里的文字一律按纯文本显示，选中的原文和模型输出不会被当成 HTML。
"""
import ctypes
from ctypes import wintypes

from PySide6.QtCore import QPoint, QPropertyAnimation, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QGuiApplication, QPainter
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout,
                               QWidget)

from scribe.translate import TARGETS, Translation

CARD_WIDTH = 380
_NOTICE_ORIGINAL_CHARS = 40  # 原文短于此显示原文，否则只报字数

_STYLE = """
QFrame#translatePill { background: #1c1c1e; border: 1px solid #3a3a3c; border-radius: 17px; }
QLabel#translateLbl { color: #98989d; font-size: 13px; padding: 0 4px 0 2px; }
QLabel#translateHint { color: #98989d; font-size: 11px; background: #1c1c1e;
                       border: 1px solid #3a3a3c; border-radius: 9px; padding: 2px 9px; }
QFrame#translatePill QPushButton {
    background: transparent; color: #f5f5f7; border: none; border-radius: 13px;
    font-size: 13px; padding: 4px 11px;
}
QFrame#translatePill QPushButton:hover { background: #0071e3; }
QFrame#translateSep { background: #3a3a3c; }

QFrame#translateCard { background: #1c1c1e; border: 1px solid #3a3a3c; border-radius: 14px; }
QLabel#cardHeader { color: #98989d; font-size: 12px; padding: 10px 0 6px 14px; }
QLabel#cardBody { color: #f5f5f7; font-size: 15px; padding: 4px 14px 12px 14px; }
QLabel#cardBody[error="true"] { color: #ff8a80; }
QScrollArea#cardScroll, QScrollArea#cardScroll > QWidget > QWidget { background: #1c1c1e; border: none; }
QScrollArea#cardScroll QScrollBar:vertical { background: transparent; width: 8px; margin: 2px; }
QScrollArea#cardScroll QScrollBar::handle:vertical { background: #555555; border-radius: 3px; min-height: 20px; }
QScrollArea#cardScroll QScrollBar::add-line:vertical, QScrollArea#cardScroll QScrollBar::sub-line:vertical { height: 0; }
QPushButton#cardX { background: transparent; color: #98989d; border: none; font-size: 15px; padding: 0 6px; }
QPushButton#cardX:hover { color: #f5f5f7; }
QLabel#cardNote { color: #98989d; font-size: 12px; padding: 0 14px 10px 14px; }
QFrame#cardBar { background: #232325; border-top: 1px solid #3a3a3c;
                 border-bottom-left-radius: 13px; border-bottom-right-radius: 13px; }
QFrame#cardBar QPushButton {
    background: #2c2c2e; color: #f5f5f7; border: 1px solid #3a3a3c; border-radius: 7px;
    font-size: 13px; padding: 5px 12px;
}
QFrame#cardBar QPushButton:hover { background: #3a3a3c; }
QFrame#cardBar QPushButton#cardPrimary { background: #0071e3; border-color: #0071e3; }
QFrame#cardBar QPushButton#cardPrimary:hover { background: #0066cc; }

QLabel#translateToast { background: #1c1c1e; color: #f5f5f7; border-radius: 18px;
                        padding: 7px 14px; font-size: 13px; }
"""


def _no_focus(widget: QWidget, title: str) -> None:
    widget.setWindowTitle(title)
    widget.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    widget.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    widget.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    widget.setStyleSheet(_STYLE)


def _flags() -> Qt.WindowType:
    return (Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowDoesNotAcceptFocus)


def _show_without_activating(widget: QWidget) -> None:
    widget.show()
    if QGuiApplication.platformName() == "windows":
        hwnd = wintypes.HWND(int(widget.winId()))
        user32 = ctypes.windll.user32
        if not user32.IsWindowVisible(hwnd):
            user32.ShowWindow(hwnd, 4)  # SW_SHOWNOACTIVATE


def _clamped(widget: QWidget, x: int, y: int) -> QPoint:
    """把窗口左上角放在 (x, y)，超出所在屏幕可用区域就内收。"""
    screen = QGuiApplication.screenAt(QPoint(x, y)) or QGuiApplication.primaryScreen()
    area = screen.availableGeometry()
    x = max(area.left(), min(x, area.right() - widget.width() + 1))
    y = max(area.top(), min(y, area.bottom() - widget.height() + 1))
    return QPoint(x, y)


def _button(text: str, handler, object_name: str = "") -> QPushButton:
    b = QPushButton(text)
    if object_name:
        b.setObjectName(object_name)
    b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.clicked.connect(handler)
    return b


class _Logo(QWidget):
    """悬浮条左侧的小标志：深色圆底，一黄一白两道杠。"""

    def __init__(self):
        super().__init__()
        self.setFixedSize(22, 22)

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QColor("#555555"))
        p.setBrush(QColor("#2a2a2d"))
        p.drawEllipse(0, 0, 21, 21)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#f2cf66"))
        p.drawRoundedRect(6, 7, 10, 2, 1, 1)
        p.setBrush(QColor("#f5f5f7"))
        p.drawRoundedRect(4, 11, 14, 3, 1.5, 1.5)


class TranslateBar(QWidget):
    """划选后出现的小条：「译成 中文 | English」。"""
    chosen = Signal(str)
    hovered = Signal(bool)

    def __init__(self):
        super().__init__(None, _flags())
        _no_focus(self, "BABEL Scribe · 划词翻译")
        shell = QVBoxLayout(self)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(4)
        pill = QFrame()
        pill.setObjectName("translatePill")
        pill.setFixedHeight(34)
        row = QHBoxLayout(pill)
        row.setContentsMargins(5, 3, 3, 3)
        row.setSpacing(2)
        row.addWidget(_Logo())
        lbl = QLabel("译成")
        lbl.setObjectName("translateLbl")
        row.addWidget(lbl)
        row.addWidget(_button("中文", lambda: self.chosen.emit("zh")))
        sep = QFrame()
        sep.setObjectName("translateSep")
        sep.setFixedSize(1, 14)
        row.addWidget(sep)
        row.addWidget(_button("English", lambda: self.chosen.emit("en")))
        shell.addWidget(pill, 0, Qt.AlignmentFlag.AlignLeft)
        self._hint = QLabel("Scribe 划词翻译 · 不需要可在托盘关闭")
        self._hint.setObjectName("translateHint")
        self._hint.setTextFormat(Qt.TextFormat.PlainText)
        self._hint.hide()
        shell.addWidget(self._hint, 0, Qt.AlignmentFlag.AlignLeft)

        self._fade = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade.setDuration(250)
        self._fade.setStartValue(1.0)
        self._fade.setEndValue(0.0)
        self._fade.finished.connect(self.hide_now)

    def hint_text(self) -> str:
        return "" if self._hint.isHidden() else self._hint.text()

    def show_at(self, x: int, y: int, *, first_time: bool) -> None:
        self._fade.stop()
        self.setWindowOpacity(1.0)
        self._hint.setVisible(first_time)
        self.layout().activate()
        self.adjustSize()
        self.move(_clamped(self, x, y))
        _show_without_activating(self)

    def fade(self) -> None:
        if self.isVisible():
            self._fade.start()

    def hide_now(self) -> None:
        self._fade.stop()
        self.setWindowOpacity(1.0)
        self.hide()

    def enterEvent(self, event):
        self.hovered.emit(True)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.hovered.emit(False)
        super().leaveEvent(event)


class TranslateCard(QWidget):
    """译文卡片：加载中 / 结果 / 出错 / 没有服务 四种内容。"""
    replace_requested = Signal()
    copy_requested = Signal()
    retry_requested = Signal()
    closed = Signal()

    def __init__(self):
        super().__init__(None, _flags())
        _no_focus(self, "BABEL Scribe · 译文")
        self.setFixedWidth(CARD_WIDTH)
        shell = QVBoxLayout(self)
        shell.setContentsMargins(0, 0, 0, 0)
        panel = QFrame()
        panel.setObjectName("translateCard")
        shell.addWidget(panel)
        col = QVBoxLayout(panel)
        self._col = col
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)
        self._header = self._label("cardHeader")
        self._body = self._label("cardBody")
        self._note = self._label("cardNote")
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 8, 0)
        top.addWidget(self._header, 1)
        self._x = _button("✕", self._close, "cardX")
        top.addWidget(self._x, 0, Qt.AlignmentFlag.AlignTop)
        col.addLayout(top)
        self._scroll = QScrollArea()
        self._scroll.setObjectName("cardScroll")
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setWidget(self._body)
        col.addWidget(self._scroll)
        col.addWidget(self._note)
        bar = QFrame()
        bar.setObjectName("cardBar")
        row = QHBoxLayout(bar)
        row.setContentsMargins(12, 10, 12, 10)
        row.setSpacing(8)
        row.addStretch(1)
        self._cancel = _button("取消 Esc", self._close)
        self._close_btn = _button("关闭 Esc", self._close)
        self._close_plain = _button("关闭", self._close)
        self._copy = _button("复制", self.copy_requested.emit)
        self._retry = _button("重试", self.retry_requested.emit)
        self._replace = _button("替换原文 ↵", self.replace_requested.emit, "cardPrimary")
        self._all_buttons = (self._cancel, self._close_btn, self._close_plain,
                             self._copy, self._retry, self._replace)
        for b in self._all_buttons:
            row.addWidget(b)
        col.addWidget(bar)

        self._pos = QPoint(0, 0)
        self._can_replace = False
        self._set_buttons()

    @staticmethod
    def _label(name: str) -> QLabel:
        lab = QLabel()
        lab.setObjectName(name)
        lab.setTextFormat(Qt.TextFormat.PlainText)
        lab.setWordWrap(True)
        lab.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        return lab

    # ---- 状态 ----
    @property
    def can_replace(self) -> bool:
        return self._can_replace

    @property
    def showing(self) -> bool:
        return self.isVisible()

    def header_text(self) -> str:
        return self._header.text()

    def body_text(self) -> str:
        return self._body.text()

    def note_text(self) -> str:
        return self._note.text()

    # ---- 内容 ----
    def show_loading(self, x: int, y: int, target: str, source: str) -> None:
        self._pos = QPoint(x, y)
        self._present(f"Scribe 译成 {TARGETS[target]}", "正在翻译…",
                      self._source_note(source), [self._cancel], editable=False)

    def show_result(self, t: Translation, target: str, *, editable: bool) -> None:
        notes = []
        if t.unchanged:
            notes.append(f"原文已经是{'英文' if target == 'en' else '中文'}，没有改动")
        if t.truncated:
            notes.append("只翻译了前 2000 字")
        buttons = [self._close_btn, self._copy] + ([self._replace] if editable else [])
        self._present(f"Scribe 译成 {TARGETS[target]} · {t.service} {t.ms / 1000:.1f} 秒",
                      t.text, "；".join(notes), buttons, editable=editable)

    def show_error(self, services: list) -> None:
        self._present("Scribe 划词翻译", f"没翻成：{'、'.join(services)} 都没有回应。",
                      "检查网络，或在设置 → 整理服务里测试连接。",
                      [self._close_plain, self._retry], editable=False, error=True)

    def show_no_service(self) -> None:
        self._present("Scribe 划词翻译", "还没有可用的翻译服务。在 设置 → 整理服务 里添加一个。",
                      "", [self._close_plain], editable=False, error=True)

    @staticmethod
    def _source_note(source: str) -> str:
        if len(source) < _NOTICE_ORIGINAL_CHARS:
            return f"原文：{source}"
        return f"原文 {len(source)} 字"

    def _present(self, header, body, note, buttons, *, editable, error=False) -> None:
        self._can_replace = editable
        self._header.setText(header)
        self._body.setText(body)
        self._body.setProperty("error", error)
        self._body.style().unpolish(self._body)
        self._body.style().polish(self._body)
        self._note.setText(note)
        self._note.setVisible(bool(note))
        self._set_buttons(buttons)
        self._fit_body()
        self.move(_clamped(self, self._pos.x(), self._pos.y()))
        _show_without_activating(self)

    def _fit_body(self) -> None:
        """正文区随内容长高，但整张卡片不超过屏幕可用高度的 60%，超出的正文在区内滚动。"""
        screen = QGuiApplication.screenAt(self._pos) or QGuiApplication.primaryScreen()
        cap = int(screen.availableGeometry().height() * 0.6)
        inner = CARD_WIDTH - 2 - 12  # 去掉卡片边框和滚动条
        self._body.setFixedWidth(inner)
        want = self._body.heightForWidth(inner)
        self._scroll.setFixedHeight(want)
        over = self._needed_height() - cap
        if over > 0:
            self._scroll.setFixedHeight(max(40, want - over))
        self.setFixedHeight(self._needed_height())

    def _needed_height(self) -> int:
        # 内层面板的布局缓存着上一次的尺寸, 不先作废就会按旧内容算高度, 长译文的结尾和按钮被切掉
        for layout in (self._col, self.layout()):
            layout.invalidate()
            layout.activate()
        return self.layout().sizeHint().height()

    def _set_buttons(self, visible=()) -> None:
        for b in self._all_buttons:
            b.setVisible(b in visible)

    def _close(self) -> None:
        self._can_replace = False
        self.hide()
        self.closed.emit()


class TranslateToast(QWidget):
    """一闪而过的小提示：「已复制」「没读到选中的文字」。"""

    def __init__(self):
        super().__init__(None, _flags())
        _no_focus(self, "BABEL Scribe · 提示")
        shell = QVBoxLayout(self)
        shell.setContentsMargins(0, 0, 0, 0)
        self._label = QLabel()
        self._label.setObjectName("translateToast")
        self._label.setTextFormat(Qt.TextFormat.PlainText)
        shell.addWidget(self._label)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

    def text(self) -> str:
        return self._label.text()

    def flash(self, text: str, x: int, y: int, ms: int = 1200) -> None:
        self._label.setText(text)
        self.layout().activate()
        self.adjustSize()
        self.move(_clamped(self, x, y))
        _show_without_activating(self)
        self._timer.start(ms)
