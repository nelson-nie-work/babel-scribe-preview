"""设置页「双引擎识别」开关下面的面板：下载进度、不能开启的原因、清理已下载文件。"""
from collections.abc import Callable

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from scribe.download import Progress
from scribe.dual_engine import Phase, SetupState
from scribe.gpu_check import DRIVER_URL, REASONS
from scribe.tray import format_gb, format_rate
from scribe.vocab_window import _label

GiB = 1024 ** 3


def _row_text(title: str, progress: Progress | None) -> str:
    if progress is None:
        return f"{title}  等待中"
    if progress.total and progress.done >= progress.total:
        return f"{title}  已就绪"
    return f"{title}  {progress.done / GiB:.1f} / {format_gb(progress.total)} · {format_rate(progress.rate)}"


def _active(progress: Progress | None) -> bool:
    return progress is not None and progress.done < progress.total


class DualEnginePanel(QWidget):
    def __init__(self, *, on_cancel: Callable[[], None], on_retry: Callable[[], None],
                 on_delete: Callable[[], None],
                 open_url: Callable[[str], None] = lambda url: QDesktopServices.openUrl(QUrl(url))):
        super().__init__()
        self._open_url = open_url
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 8)
        layout.setSpacing(3)
        self.runtime_row = _label("", "vocabQuiet")
        self.model_row = _label("", "vocabQuiet")
        self.source = _label("", "vocabQuiet")
        self.reason = _label("", "vocabError")
        self.reason.setWordWrap(True)
        for widget in (self.runtime_row, self.model_row, self.source, self.reason):
            layout.addWidget(widget)
        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 2, 0, 0)
        self.cancel = QPushButton("取消", autoDefault=False)
        self.retry = QPushButton("重试", autoDefault=False)
        self.update_driver = QPushButton("更新驱动", autoDefault=False)
        self.delete = QPushButton("", autoDefault=False)
        self.cancel.clicked.connect(on_cancel)
        self.retry.clicked.connect(on_retry)
        self.update_driver.clicked.connect(self._on_update_driver)   # 绑定方法：引用 self 的 lambda 会形成循环引用
        self.delete.clicked.connect(on_delete)
        for button in (self.cancel, self.retry, self.update_driver, self.delete):
            buttons.addWidget(button)
        buttons.addStretch()
        layout.addLayout(buttons)
        self.setVisible(False)

    def _on_update_driver(self) -> None:
        self._open_url(DRIVER_URL)

    def show_state(self, state: SetupState, *, enabled: bool) -> None:
        downloading = state.phase is Phase.DOWNLOADING
        failed = state.phase is Phase.FAILED
        offer_delete = not downloading and not failed and not enabled and state.files_bytes > 0
        self.runtime_row.setText(_row_text("NVIDIA 运行库", state.runtime))
        self.model_row.setText(_row_text("Whisper 模型", state.model))
        current = state.model if _active(state.model) else state.runtime if _active(state.runtime) else None
        self.source.setText(f"来源 {current.source}" if current is not None and current.source else "")
        self.reason.setText(REASONS.get(state.reason, "") if failed else "")
        self.delete.setText(f"删除已下载的双引擎文件（约 {format_gb(state.files_bytes)}）")
        self.runtime_row.setVisible(downloading)
        self.model_row.setVisible(downloading)
        self.source.setVisible(downloading and bool(self.source.text()))
        self.cancel.setVisible(downloading)
        self.reason.setVisible(failed)
        self.update_driver.setVisible(failed and state.reason == "old_driver")
        self.retry.setVisible(failed and state.reason in ("download", "load"))
        self.delete.setVisible(offer_delete)
        self.setVisible(downloading or failed or offer_delete)
