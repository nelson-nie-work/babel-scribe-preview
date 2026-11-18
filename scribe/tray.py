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




def format_gb(n: int) -> str:
    return "%.1f GB" % (n / 1024 ** 3)


def format_rate(bps: float) -> str:
    return "%.1f MB/s" % (bps / 1024 ** 2)


def format_download(p) -> str:
    """下载进度，如「38% · 12.0 MB/s」。"""
    percent = int(p.done * 100 / p.total) if p.total else 0
    return "%d%% · %s" % (percent, format_rate(p.rate))


def _sec(ms: Optional[int]) -> str:
    return "—" if ms is None else "%.2f 秒" % (ms / 1000)


def format_stats(items: list) -> str:
    """前两个服务最近 10 次的平均耗时, 如「最近:OpenAI 0.69 秒 · DeepSeek 0.76 秒」。"""
    items = list(items)[:2]
    if all(ms is None for _name, ms in items):
        return "最近:—"
    return "最近:" + " · ".join("%s %s" % (name, _sec(ms)) for name, ms in items)


class Tray(QSystemTrayIcon):
    def __init__(self, on_quit: Callable[[], None],
                 services: Callable[[], list[tuple[str, str]]] = lambda: [],
                 on_service: Callable[[str], None] = lambda sid: None,
                 stats: Callable[[], str] = lambda: "最近:—",
                 *, on_main: Callable[[], None] | None = None,
                 recent: Callable[[], list[tuple[str, str]]] | None = None,
                 copy_recent: Callable[[str], None] | None = None,
                 on_click: Callable[[], None] | None = None):
        super().__init__(make_icon())
        self.setToolTip(APP_NAME)
        self._stats = stats
        self._menu = QMenu()
        self._status = QAction("加载模型中…", self._menu)
        self._status.setEnabled(False)
        self._menu.addAction(self._status)
        self._menu.addSeparator()
        if on_click is not None:
            self.activated.connect(
                lambda reason: on_click() if reason == self.ActivationReason.Trigger else None
            )
        self._services, self._on_service = services, on_service
        self.service_actions = {}
        self._service_group = QActionGroup(self._menu)
        self._service_group.setExclusive(True)
        self._service_items = []
        self.stats_action = QAction(stats(), self._menu)
        self.stats_action.setEnabled(False)
        self._menu.addAction(self.stats_action)
        self._menu.aboutToShow.connect(self._refresh_services)
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

    def _refresh_services(self) -> None:
        """菜单打开前按当前顺序重建服务项, 勾选第 1 个;选别的就交给 on_service 移到第 1 位。"""
        for item in self._service_items:
            self._menu.removeAction(item)
            self._service_group.removeAction(item)
        self._service_items, self.service_actions = [], {}
        try:
            entries = list(self._services())
        except Exception:
            entries = []
        before = self.stats_action
        if not entries:
            empty = QAction("未配置整理服务", self._menu)
            empty.setEnabled(False)
            self._menu.insertAction(before, empty)
            self._service_items.append(empty)
            return
        for index, (sid, label) in enumerate(entries):
            act = QAction("整理:" + label.replace("&", "&&"), self._menu, checkable=True)
            act.setChecked(index == 0)
            act.triggered.connect(lambda _checked=False, sid=sid: self._on_service(sid))
            self._service_group.addAction(act)
            self._menu.insertAction(before, act)
            self._service_items.append(act)
            self.service_actions[sid] = act

    def _refresh_stats(self) -> None:
        try:
            self.stats_action.setText(self._stats())
        except Exception:
            self.stats_action.setText("最近:—")

    def set_status(self, text: str, detail: str = "") -> None:
        self._status.setText(text)
        tip = "%s · %s" % (APP_NAME, text)
        self.setToolTip(tip + (" · " + detail if detail else ""))
