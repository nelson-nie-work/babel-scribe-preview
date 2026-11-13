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


def test_mode_actions_exclusive_and_checked(qapp_offscreen):
    got = []
    t = Tray(on_quit=lambda: None, on_mode=got.append, mode="deepseek", stats=lambda: "最近:—")
    acts = t.mode_actions
    assert acts["deepseek"].text() == "整理:DeepSeek" and acts["deepseek"].isChecked()
    assert not acts["gpt"].isChecked() and not acts["auto"].isChecked()
    acts["auto"].trigger()
    assert got == ["auto"]
    assert [m for m, a in acts.items() if a.isChecked()] == ["auto"]
    t.hide()


def test_stats_line_refreshed_before_menu_shows(qapp_offscreen):
    calls = iter(["最近:—", "最近:GPT 0.69 秒 · DeepSeek —"])
    t = Tray(on_quit=lambda: None, on_mode=lambda m: None, mode="gpt", stats=lambda: next(calls))
    t.contextMenu().aboutToShow.emit()
    assert t.stats_action.text() == "最近:GPT 0.69 秒 · DeepSeek —"
    assert not t.stats_action.isEnabled()
    t.hide()


def test_format_stats():
    assert format_stats(690, 760) == "最近:GPT 0.69 秒 · DeepSeek 0.76 秒"
    assert format_stats(None, 760) == "最近:GPT — · DeepSeek 0.76 秒"
    assert format_stats(None, None) == "最近:—"
