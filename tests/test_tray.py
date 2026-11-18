from types import SimpleNamespace

from scribe import learn_dialog, vocab
from scribe.tray import Tray, format_stats, make_icon
from scribe.vocab_store import VocabularyStore
from scribe.vocab_window import VocabularyWindow


def test_app_icon_contains_native_shell_sizes(qapp_offscreen):
    sizes = {size.width() for size in make_icon().availableSizes()}
    assert {16, 24, 32, 48, 256} <= sizes


def test_tray_exposes_one_entry_for_the_main_window(qapp_offscreen):
    opened = []
    tray = Tray(on_quit=lambda: None, on_main=lambda: opened.append(True))
    actions = {item.text(): item for item in tray.contextMenu().actions()}
    assert not ({"打开词库", "设置", "听写历史"} & actions.keys())
    action = actions["打开主界面"]
    action.trigger()
    assert opened == [True]
    tray.activated.emit(Tray.ActivationReason.Trigger)
    assert opened == [True]
    tray.activated.emit(Tray.ActivationReason.DoubleClick)
    assert opened == [True, True]
    tray.hide()


def test_vocab_tray_reopens_a_window_hidden_by_windows(qapp_offscreen, tmp_path, monkeypatch):
    window = VocabularyWindow(VocabularyStore(tmp_path, vocab.load_terms(tmp_path)))
    window.show()
    show_commands = []
    user32 = SimpleNamespace(
        IsWindowVisible=lambda _hwnd: bool(show_commands),
        ShowWindow=lambda _hwnd, command: show_commands.append(command),
    )
    monkeypatch.setattr(learn_dialog.QApplication, "platformName", staticmethod(lambda: "windows"))
    monkeypatch.setattr(learn_dialog.ctypes, "windll", SimpleNamespace(user32=user32), raising=False)
    assert window.open_from_tray()
    assert show_commands == [4]
    window.close()


def test_service_menu_checks_first_and_reports_choice(qapp_offscreen):
    picked = []
    t = Tray(on_quit=lambda: None, services=lambda: [("a", "OpenAI"), ("b", "DeepSeek")],
             on_service=picked.append)
    t.contextMenu().aboutToShow.emit()
    acts = t.service_actions
    assert acts["a"].text() == "整理:OpenAI" and acts["a"].isChecked()
    assert not acts["b"].isChecked()
    acts["b"].trigger()
    assert picked == ["b"]
    t.hide()


def test_service_menu_without_services(qapp_offscreen):
    t = Tray(on_quit=lambda: None, services=lambda: [], on_service=lambda sid: None)
    t.contextMenu().aboutToShow.emit()
    texts = [a.text() for a in t.contextMenu().actions()]
    assert "未配置整理服务" in texts and t.service_actions == {}
    t.hide()


def test_service_menu_follows_new_order(qapp_offscreen):
    order = [[("a", "OpenAI"), ("b", "DeepSeek")]]
    t = Tray(on_quit=lambda: None, services=lambda: order[0], on_service=lambda sid: None)
    t.contextMenu().aboutToShow.emit()
    order[0] = [("b", "DeepSeek"), ("a", "OpenAI")]
    t.contextMenu().aboutToShow.emit()
    assert t.service_actions["b"].isChecked() and not t.service_actions["a"].isChecked()
    t.hide()


def test_stats_line_refreshed_before_menu_shows(qapp_offscreen):
    calls = iter(["最近:—", "最近:OpenAI 0.69 秒 · DeepSeek —"])
    t = Tray(on_quit=lambda: None, stats=lambda: next(calls))
    t.contextMenu().aboutToShow.emit()
    assert t.stats_action.text() == "最近:OpenAI 0.69 秒 · DeepSeek —"
    assert not t.stats_action.isEnabled()
    t.hide()


def test_format_stats_uses_service_names():
    assert format_stats([("OpenAI", 690), ("DeepSeek", None)]) == "最近:OpenAI 0.69 秒 · DeepSeek —"
    assert format_stats([("OpenAI", 690), ("DeepSeek", 760), ("智谱", 900)]) == "最近:OpenAI 0.69 秒 · DeepSeek 0.76 秒"
    assert format_stats([("OpenAI", None)]) == "最近:—"
    assert format_stats([]) == "最近:—"


def test_format_download():
    from scribe import tray
    from scribe.download import Progress
    assert tray.format_download(Progress(380, 1000, 12.0 * 1024 ** 2, "hf-mirror.com")) == "38% · 12.0 MB/s"
    assert tray.format_gb(int(1.3 * 1024 ** 3)) == "1.3 GB"
    assert tray.format_rate(0) == "0.0 MB/s"
    assert tray.format_download(Progress(0, 0, 0.0, "")) == "0% · 0.0 MB/s"


def test_tray_click_calls_on_click(qapp_offscreen):
    hits = []
    t = Tray(on_quit=lambda: None, on_click=lambda: hits.append(1))
    t.activated.emit(t.ActivationReason.Trigger)
    t.activated.emit(t.ActivationReason.Context)
    assert hits == [1]
    t.hide()


def test_set_status_detail_goes_to_tooltip(qapp_offscreen):
    t = Tray(on_quit=lambda: None)
    t.set_status("下载模型 38% · 12.0 MB/s", "来源 hf-mirror.com")
    assert t.toolTip() == "BABEL Scribe · 下载模型 38% · 12.0 MB/s · 来源 hf-mirror.com"
    t.set_status("就绪")
    assert t.toolTip() == "BABEL Scribe · 就绪"
    t.hide()
