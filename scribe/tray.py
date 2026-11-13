"""托盘图标与右键菜单。"""
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtGui import QAction, QActionGroup, QIcon
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from scribe.config import APP_NAME


APP_ICON_PATH = Path(__file__).with_name("assets") / "scribe.ico"


def make_icon() -> QIcon:
    """托盘、窗口与 Windows 快捷方式共用的多尺寸图标。"""
    return QIcon(str(APP_ICON_PATH))


MODES = (("gpt", "整理:GPT"), ("deepseek", "整理:DeepSeek"), ("auto", "整理:自动"))


def _sec(ms: Optional[int]) -> str:
    return "—" if ms is None else "%.2f 秒" % (ms / 1000)


def format_stats(gpt_ms: Optional[int], deepseek_ms: Optional[int]) -> str:
    """最近 10 次两家平均耗时, 如「最近:GPT 0.69 秒 · DeepSeek 0.76 秒」。"""
    if gpt_ms is None and deepseek_ms is None:
        return "最近:—"
    return "最近:GPT %s · DeepSeek %s" % (_sec(gpt_ms), _sec(deepseek_ms))


class Tray(QSystemTrayIcon):
    def __init__(self, on_quit: Callable[[], None], on_mode: Callable[[str], None] = lambda m: None,
                 mode: str = "gpt", stats: Callable[[], str] = lambda: "最近:—",
                 *, on_main: Callable[[], None] | None = None,
                 recent: Callable[[], list[tuple[str, str]]] | None = None,
                 copy_recent: Callable[[str], None] | None = None):
        super().__init__(make_icon())
        self.setToolTip(APP_NAME)
        self._stats = stats
        self._menu = QMenu()
        self._status = QAction("加载模型中…", self._menu)
        self._status.setEnabled(False)
        self._menu.addAction(self._status)
        self._menu.addSeparator()
        group = QActionGroup(self._menu)
        group.setExclusive(True)
        self.mode_actions = {}
        for key, label in MODES:
            act = QAction(label, self._menu, checkable=True)
            act.setChecked(key == mode)
            act.triggered.connect(lambda checked=False, key=key: on_mode(key))
            group.addAction(act)
            self._menu.addAction(act)
            self.mode_actions[key] = act
        self.stats_action = QAction(stats(), self._menu)
        self.stats_action.setEnabled(False)
        self._menu.addAction(self.stats_action)
        self._menu.aboutToShow.connect(self._refresh_stats)
        self._menu.addSeparator()
        if on_main is not None:
            main_action = QAction("打开主界面", self._menu)
            main_action.triggered.connect(on_main)
            self._menu.addAction(main_action)
            self.activated.connect(
                lambda reason: on_main() if reason == self.ActivationReason.DoubleClick else None
            )
        if recent is not None and copy_recent is not None:
            recent_menu = self._menu.addMenu("最近听写")

            def refresh_recent() -> None:
                recent_menu.clear()
                for text, application in recent():
                    excerpt = " ".join(text.split())
                    if len(excerpt) > 25:
                        excerpt = excerpt[:25] + "…"
                    item = recent_menu.addAction(f"{application}  {excerpt}".replace("&", "&&"))
                    item.triggered.connect(lambda _checked=False, value=text: copy_recent(value))
                if recent_menu.isEmpty():
                    recent_menu.addAction("还没有听写记录").setEnabled(False)

            recent_menu.aboutToShow.connect(refresh_recent)
        quit_action = QAction("退出", self._menu)
        quit_action.triggered.connect(on_quit)
        self._menu.addAction(quit_action)
        self.setContextMenu(self._menu)
        self.show()

    def _refresh_stats(self) -> None:
        try:
            self.stats_action.setText(self._stats())
        except Exception:
            self.stats_action.setText("最近:—")

    def set_status(self, text: str) -> None:
        self._status.setText(text)
        self.setToolTip("%s · %s" % (APP_NAME, text))

    def set_mode(self, mode: str) -> None:
        self.mode_actions[mode].setChecked(True)
