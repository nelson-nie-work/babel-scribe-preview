"""灵动胶囊(概念稿方向 A):屏幕底部居中的深色胶囊,永不抢焦点。

CapsuleModel 是纯状态;CapsuleWindow 只按 model 绘制与显示/隐藏。
"""
import collections
import ctypes
import math
import time
from typing import Optional

from scribe.config import DONE_HIDE_MS, ERROR_HIDE_MS

_LEVEL_FULL = 0.08      # RMS 达到这个值, 声波条满格


class CapsuleModel:
    def __init__(self):
        self.state = "hidden"
        self.locked = False
        self.message = ""
        self.preview = ""          # 实时字幕(只是预览, 不进输入框)
        self._t0 = 0.0
        self._levels = collections.deque(maxlen=9)

    # 状态切换
    def listening(self, t0: float) -> None:
        self.state, self.locked, self.message, self._t0 = "listening", False, "", t0
        self.preview = ""
        self._levels.clear()

    def lock(self) -> None:
        self.locked = True

    def loading(self) -> None:
        self.state, self.message = "loading", "加载中"

    def processing(self) -> None:
        self.state, self.message = "processing", "识别中"

    def polishing(self) -> None:
        self.state, self.message = "processing", "整理中"

    def done(self) -> None:
        self.state, self.message, self.preview = "done", "已输入", ""

    def error(self, message: str) -> None:
        self.state, self.message, self.preview = "error", message, ""

    def hide(self) -> None:
        self.state, self.message, self.preview = "hidden", "", ""

    def set_preview(self, text: str) -> None:
        """只在聆听中生效;松手后的迟到预览一律丢弃。"""
        if self.state == "listening":
            self.preview = text

    def push_level(self, rms: float) -> None:
        self._levels.append(min(1.0, rms / _LEVEL_FULL))

    # 只读
    @property
    def bars(self) -> list:
        return list(self._levels)

    @property
    def hide_after_ms(self) -> Optional[int]:
        return {"done": DONE_HIDE_MS, "error": ERROR_HIDE_MS}.get(self.state)

    def timer_text(self, now: float) -> str:
        s = max(0, int(now - self._t0))
        return "%d:%02d" % (s // 60, s % 60)


# ---------------------------------------------------------------- 绘制

from PySide6.QtCore import QPointF, QPropertyAnimation, QRectF, Qt, QTimer  # noqa: E402
from PySide6.QtGui import QColor, QFont, QFontMetrics, QGuiApplication, QPainter, QPainterPath, QPen  # noqa: E402
from PySide6.QtWidgets import QWidget  # noqa: E402

_BG = QColor(0x1C, 0x1C, 0x1E, 245)
_RED = QColor("#FF453A")
_GREEN = QColor("#30D158")
_YELLOW = QColor("#FFD60A")
_WHITE = QColor("#FFFFFF")
_GRAY = QColor("#8E8E93")
_H, _PAD, _GAP, _BOTTOM = 40, 16, 10, 48
_MAX_W = 520            # 胶囊最宽(含左右内边距)
_WAVE_W = 9 * 3 + 8 * 3


class CapsuleWindow(QWidget):
    def __init__(self, model: CapsuleModel):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                         | Qt.WindowDoesNotAcceptFocus | Qt.WindowTransparentForInput)
        self.model = model
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFocusPolicy(Qt.NoFocus)
        self._font = QFont("Microsoft YaHei UI")
        self._font.setPixelSize(13)
        self._font.setWeight(QFont.Medium)
        self._pfont = QFont("Microsoft YaHei UI")       # 预览文字(概念稿 A+: 14 px)
        self._pfont.setPixelSize(14)
        self._anim = QTimer(self, interval=50, timeout=self.update)
        self._fade = QPropertyAnimation(self, b"windowOpacity", self, duration=200)
        self._fade.finished.connect(self._after_fade)
        self._gen = 0           # 防止旧的自动隐藏计时器收起新状态
        self._exstyle_done = False

    # 显示控制
    def refresh(self) -> None:
        self._gen += 1
        m = self.model
        if m.state == "hidden":
            self._anim.stop()
            self.hide()
            return
        self._fade.stop()
        self.setWindowOpacity(1.0)
        self._place()
        if not self.isVisible():
            self.show()
        if m.state in ("listening", "loading", "processing"):
            self._anim.start()
        else:
            self._anim.stop()
        self.update()
        if m.hide_after_ms:
            gen = self._gen
            QTimer.singleShot(m.hide_after_ms, lambda: self._auto_hide(gen))

    def _auto_hide(self, gen: int) -> None:
        if gen != self._gen:
            return
        self._fade.setStartValue(1.0)
        self._fade.setEndValue(0.0)
        self._fade.start()

    def _after_fade(self) -> None:
        if self.windowOpacity() <= 0.01:
            self.model.hide()
            self.hide()
            self.setWindowOpacity(1.0)

    def _fixed_width(self) -> int:
        """除预览文字以外的内容宽度。"""
        m, fm = self.model, QFontMetrics(self._font)
        if m.state == "listening":
            w = 8 + _GAP + _WAVE_W + _GAP + fm.horizontalAdvance("0:00")
            return w + (_GAP + 12 if m.locked else 0)
        if m.state == "processing" and m.preview:
            return 22 + _GAP + fm.horizontalAdvance(m.message) + _GAP
        icon = 16 if m.state in ("done", "error") else 22
        return icon + _GAP + fm.horizontalAdvance(m.message)

    def _preview_text(self) -> tuple:
        """(要画的预览文字, 它占的宽度);放不下时从左边截断。"""
        m = self.model
        if not m.preview or m.state not in ("listening", "processing"):
            return "", 0
        fm = QFontMetrics(self._pfont)
        gap = _GAP if m.state == "listening" else 0
        room = _MAX_W - 2 * _PAD - self._fixed_width() - gap
        text = m.preview
        if fm.horizontalAdvance(text) > room:       # 放不下: 宽度封顶不再抖动, 旧字从左边滚出
            return fm.elidedText(text.lstrip("…"), Qt.ElideLeft, room), room + gap
        return text, fm.horizontalAdvance(text) + gap

    def _content_width(self) -> int:
        return self._fixed_width() + self._preview_text()[1]

    def _place(self) -> None:
        w = min(_MAX_W, self._content_width() + 2 * _PAD)
        geo = QGuiApplication.primaryScreen().availableGeometry()
        self.setGeometry(geo.x() + (geo.width() - w) // 2, geo.y() + geo.height() - _BOTTOM - _H, w, _H)

    def showEvent(self, event):
        super().showEvent(event)
        if not self._exstyle_done:
            # 再加一道 Win32 保险: 不激活、不进任务栏
            try:
                u32 = ctypes.windll.user32
                u32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
                u32.GetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int]
                u32.SetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
                hwnd, gwl = int(self.winId()), -20
                ex = u32.GetWindowLongPtrW(hwnd, gwl)
                u32.SetWindowLongPtrW(hwnd, gwl, ex | 0x08000000 | 0x00000080)   # NOACTIVATE | TOOLWINDOW
            except Exception:
                pass
            self._exstyle_done = True

    # 绘制
    def paintEvent(self, event):
        m = self.model
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(_BG)
        p.drawRoundedRect(QRectF(0, 0, self.width(), _H), _H / 2, _H / 2)
        p.setFont(self._font)
        x, cy, now = float(_PAD), _H / 2, time.monotonic()
        if m.state == "listening":
            red = QColor(_RED)
            red.setAlphaF(1.0 if int(now / 0.6) % 2 == 0 else 0.35)
            p.setBrush(red)
            p.drawEllipse(QPointF(x + 4, cy), 4, 4)
            x += 8 + _GAP
            bars = m.bars
            bars = [0.0] * (9 - len(bars)) + bars
            p.setBrush(_WHITE)
            for v in bars:
                h = 6 + 18 * v
                p.drawRoundedRect(QRectF(x, cy - h / 2, 3, h), 1.5, 1.5)
                x += 6
            x += _GAP - 3
            if m.locked:
                self._draw_lock(p, x, cy)
                x += 12 + _GAP
            text, tw = self._preview_text()
            if text:
                p.setFont(self._pfont)
                p.setPen(_WHITE)
                p.drawText(QRectF(x, 0, tw - _GAP, _H), Qt.AlignVCenter | Qt.AlignLeft, text)
                p.setFont(self._font)
                x += tw
            p.setPen(_WHITE)
            p.drawText(QRectF(x, 0, self.width() - x, _H), Qt.AlignVCenter | Qt.AlignLeft, m.timer_text(now))
        elif m.state in ("loading", "processing"):
            for i in range(3):
                c = QColor(_WHITE)
                c.setAlphaF(0.25 + 0.75 * max(0.0, math.sin((now * 5.2) - i * 0.9)))
                p.setBrush(c)
                p.drawEllipse(QPointF(x + 2.5 + i * 8, cy), 2.5, 2.5)
            x += 22 + _GAP
            p.setPen(_WHITE)
            p.drawText(QRectF(x, 0, self.width() - x, _H), Qt.AlignVCenter | Qt.AlignLeft, m.message)
            text, _tw = self._preview_text()
            if m.state == "processing" and text:     # spec §2.2 + 概念稿 A+: 「整理中」后跟变灰的预览
                x += QFontMetrics(self._font).horizontalAdvance(m.message) + _GAP
                p.setFont(self._pfont)
                p.setPen(_GRAY)
                p.drawText(QRectF(x, 0, self.width() - x - _PAD, _H), Qt.AlignVCenter | Qt.AlignLeft, text)
        elif m.state in ("done", "error"):
            if m.state == "done":
                self._draw_check(p, x, cy)
            else:
                self._draw_warn(p, x, cy)
            x += 16 + _GAP
            p.setPen(_WHITE)
            p.drawText(QRectF(x, 0, self.width() - x, _H), Qt.AlignVCenter | Qt.AlignLeft, m.message)
        p.end()

    @staticmethod
    def _draw_check(p, x, cy):
        p.setPen(Qt.NoPen)
        p.setBrush(_GREEN)
        p.drawEllipse(QPointF(x + 8, cy), 7, 7)
        pen = QPen(_WHITE, 1.8, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        p.setPen(pen)
        path = QPainterPath(QPointF(x + 4.8, cy + 0.2))
        path.lineTo(x + 6.9, cy + 2.3)
        path.lineTo(x + 11.2, cy - 2.3)
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)

    @staticmethod
    def _draw_warn(p, x, cy):
        p.setPen(Qt.NoPen)
        p.setBrush(_YELLOW)
        tri = QPainterPath(QPointF(x + 8, cy - 6.2))
        tri.lineTo(x + 15, cy + 6)
        tri.lineTo(x + 1, cy + 6)
        tri.closeSubpath()
        p.drawPath(tri)
        p.setPen(QPen(_BG, 1.6, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(QPointF(x + 8, cy - 1.8), QPointF(x + 8, cy + 1.8))
        p.drawPoint(QPointF(x + 8, cy + 3.8))

    @staticmethod
    def _draw_lock(p, x, cy):
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(_WHITE, 1.4))
        p.drawArc(QRectF(x + 2.5, cy - 7, 7, 8), 0, 180 * 16)
        p.setPen(Qt.NoPen)
        p.setBrush(_WHITE)
        p.drawRoundedRect(QRectF(x + 1, cy - 2.5, 10, 8), 1.5, 1.5)


if __name__ == "__main__":
    # 目视检查: python -m scribe.capsule
    import sys

    from PySide6.QtWidgets import QApplication

    app = QApplication(sys.argv)
    model = CapsuleModel()
    win = CapsuleWindow(model)
    t0 = time.monotonic()
    level = QTimer(interval=50, timeout=lambda: (model.push_level(0.02 + 0.06 * abs(math.sin(time.monotonic() * 3))), win.update()))
    from scribe.preview import tail_text
    said = "帮我把上周的周报整理一下发到群里然后再把下周的会议安排发给大家确认一下时间"
    steps = [(0, lambda: (model.listening(time.monotonic()), level.start()))]
    for i in range(2, len(said) + 1, 3):       # 实时字幕逐步变长, 超过 28 字后左侧出现"…"、宽度封顶
        steps.append((i * 120, lambda i=i: model.set_preview(tail_text(said[:i]))))
    t = len(said) * 120 + 600
    steps += [
        (t, lambda: model.lock()),
        (t + 1500, lambda: (level.stop(), model.polishing())),
        (t + 3000, lambda: model.done()),
        (t + 4500, lambda: model.error("整理服务不可用,已输入原文")),
        (t + 7000, app.quit),
    ]
    for ms, fn in steps:
        QTimer.singleShot(ms, lambda fn=fn: (fn(), win.refresh()))
    app.exec()
