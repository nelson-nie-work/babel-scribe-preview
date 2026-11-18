from scribe import settings
from scribe.download import Progress
from scribe.dual_engine import Phase, SetupState
from scribe.gpu_check import DRIVER_URL, REASONS

GiB = 1024 ** 3
MiB = 1024 ** 2


def make_panel(**kw):
    from scribe.dual_engine_panel import DualEnginePanel
    hits = {"cancel": 0, "retry": 0, "delete": 0, "urls": []}
    panel = DualEnginePanel(on_cancel=lambda: hits.__setitem__("cancel", hits["cancel"] + 1),
                            on_retry=lambda: hits.__setitem__("retry", hits["retry"] + 1),
                            on_delete=lambda: hits.__setitem__("delete", hits["delete"] + 1),
                            open_url=hits["urls"].append)
    panel.show()
    return panel, hits


def test_panel_downloading_shows_two_rows_source_and_cancel(qapp_offscreen):
    panel, hits = make_panel()
    runtime = Progress(GiB * 4 // 10, GiB * 13 // 10, 12 * MiB, "pypi.tuna.tsinghua.edu.cn")
    panel.show_state(SetupState(Phase.DOWNLOADING, runtime=runtime, model=None), enabled=True)
    assert panel.isVisible()
    assert panel.runtime_row.text() == "NVIDIA 运行库  0.4 / 1.3 GB · 12.0 MB/s"
    assert panel.model_row.text() == "Whisper 模型  等待中"
    assert panel.source.text() == "来源 pypi.tuna.tsinghua.edu.cn"
    assert panel.cancel.isVisible() and not panel.retry.isVisible() and not panel.delete.isVisible()
    panel.cancel.click()
    assert hits["cancel"] == 1


def test_panel_finished_row_shows_ready_and_source_follows_active_row(qapp_offscreen):
    panel, _hits = make_panel()
    state = SetupState(Phase.DOWNLOADING, runtime=Progress(10, 10, 0.0, "files.pythonhosted.org"),
                       model=Progress(GiB // 2, GiB * 16 // 10, 3 * MiB, "hf-mirror.com"))
    panel.show_state(state, enabled=True)
    assert panel.runtime_row.text() == "NVIDIA 运行库  已就绪"
    assert panel.model_row.text() == "Whisper 模型  0.5 / 1.6 GB · 3.0 MB/s"
    assert panel.source.text() == "来源 hf-mirror.com"


def test_panel_old_driver_offers_update_link(qapp_offscreen):
    panel, hits = make_panel()
    panel.show_state(SetupState(Phase.FAILED, reason="old_driver"), enabled=False)
    assert panel.reason.text() == REASONS["old_driver"]
    assert panel.update_driver.isVisible() and not panel.retry.isVisible()
    assert not panel.runtime_row.isVisible() and not panel.cancel.isVisible()
    panel.update_driver.click()
    assert hits["urls"] == [DRIVER_URL]


def test_panel_download_failure_offers_retry(qapp_offscreen):
    panel, hits = make_panel()
    for reason in ("download", "load"):
        panel.show_state(SetupState(Phase.FAILED, reason=reason), enabled=True)
        assert panel.reason.text() == REASONS[reason]
        assert panel.retry.isVisible() and not panel.update_driver.isVisible()
    panel.retry.click()
    assert hits["retry"] == 1


def test_panel_no_gpu_has_reason_only(qapp_offscreen):
    panel, _hits = make_panel()
    panel.show_state(SetupState(Phase.FAILED, reason="no_gpu"), enabled=False)
    assert panel.reason.text() == REASONS["no_gpu"]
    assert not panel.retry.isVisible() and not panel.update_driver.isVisible()


def test_panel_offers_delete_only_when_off_with_files(qapp_offscreen):
    panel, hits = make_panel()
    files = int(3.9 * GiB)
    panel.show_state(SetupState(Phase.IDLE, files_bytes=files), enabled=False)
    assert panel.isVisible() and panel.delete.isVisible()
    assert panel.delete.text() == "删除已下载的双引擎文件（约 3.9 GB）"
    panel.delete.click()
    assert hits["delete"] == 1
    panel.show_state(SetupState(Phase.READY, files_bytes=files), enabled=True)
    assert not panel.isVisible()
    panel.show_state(SetupState(Phase.IDLE, files_bytes=0), enabled=False)
    assert not panel.isVisible()


def _page(prefs, update, panel=None, state=None):
    from scribe.general_page import GeneralPage
    page = GeneralPage(lambda: prefs, update, lambda: [], lambda: False, lambda: True,
                       dual_panel=panel, dual_state=state)
    page.sync()
    return page


def test_general_page_hint_while_preparing(qapp_offscreen):
    panel, _hits = make_panel()
    prefs = {**settings.DEFAULTS, "dual_engine": True}
    state = SetupState(Phase.DOWNLOADING)
    page = _page(prefs, lambda k, v: None, panel, lambda: state)
    assert page.engine_hint.text() == "准备中 · 先用单引擎听写"
    assert panel.cancel.isVisibleTo(panel)


def test_general_page_hint_when_ready(qapp_offscreen):
    panel, _hits = make_panel()
    prefs = {**settings.DEFAULTS, "dual_engine": True}
    page = _page(prefs, lambda k, v: None, panel, lambda: SetupState(Phase.READY))
    assert page.engine_hint.text() == "SenseVoice 与 Whisper 一起识别。"


def test_general_page_refused_dual_shows_short_status(qapp_offscreen):
    panel, _hits = make_panel()
    prefs = dict(settings.DEFAULTS)

    def update(key, value):
        raise ValueError(REASONS["no_gpu"])

    page = _page(prefs, update, panel, lambda: SetupState(Phase.FAILED, reason="no_gpu"))
    page.controls["dual_engine"].click()
    assert page.status.text() == "未开启双引擎识别。"
    assert not page.controls["dual_engine"].isChecked()
    assert panel.reason.text() == REASONS["no_gpu"]


def test_panel_is_freed_without_garbage_collector(qapp_offscreen):
    """面板槽函数不能引用自身形成循环，否则只能等垃圾回收释放，进程退出时可能晚于 QApplication 而崩溃。"""
    import gc
    import weakref

    from scribe.dual_engine_panel import DualEnginePanel
    gc.collect()
    gc.disable()
    try:
        panel = DualEnginePanel(on_cancel=print, on_retry=print, on_delete=print, open_url=print)
        panel.show_state(SetupState(Phase.FAILED, reason="no_gpu"), enabled=False)
        ref = weakref.ref(panel)
        del panel
        assert ref() is None
    finally:
        gc.enable()


def test_sync_dual_updates_panel_without_rebuilding_service_list(qapp_offscreen):
    from PySide6.QtWidgets import QWidget

    from scribe.general_page import GeneralPage

    class Services(QWidget):
        refreshes = 0

        def refresh(self):
            Services.refreshes += 1

    panel, _hits = make_panel()
    services = Services()
    prefs = {**settings.DEFAULTS, "dual_engine": True}
    holder = {"state": SetupState(Phase.READY)}
    page = GeneralPage(lambda: prefs, lambda k, v: None, lambda: [], lambda: False, lambda: True, services,
                       dual_panel=panel, dual_state=lambda: holder["state"])
    page.sync()
    before = Services.refreshes
    holder["state"] = SetupState(Phase.DOWNLOADING, runtime=Progress(1, 10, 0.0, "a"))
    page.sync_dual()
    assert Services.refreshes == before
    assert page.engine_hint.text() == "准备中 · 先用单引擎听写"
    assert panel.runtime_row.text().startswith("NVIDIA 运行库")
