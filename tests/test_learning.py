from scribe.learn_dialog import LearnDialog, confirm_alias, reveal_learning_dialog
from scribe.learning import suggest_alias


def test_full_correction_suggests_alias():
    assert suggest_alias("把结果发给欣桥。", "把结果发给新桥。") == ("欣桥", "新桥")
    assert suggest_alias("打开备播项目", "打开BABEL项目") == ("备播", "BABEL")


def test_corrected_fragment_in_user_example_can_be_matched_without_whole_sentence():
    original = "请把清样桥的版本标成待复核。"
    assert suggest_alias(original, "把青砚桥的版本标成") == ("清样", "青砚")
    assert suggest_alias(original, "青砚桥的版本标成") is None


def test_known_canonical_term_keeps_the_right_context():
    assert suggest_alias("新乔说可以。", "新桥说可以。", known_terms=["新桥"]) == ("新乔", "新桥")


def test_selecting_just_the_corrected_word_requires_unique_pinyin_match():
    assert suggest_alias("把结果发给欣桥。", "新桥") == ("欣桥", "新桥")
    assert suggest_alias("给欣桥和欣桥各发一份。", "新桥") is None
    assert suggest_alias("欣桥或新桥都可以。", "新桥") is None


def test_suggestions_skip_unrelated_or_overbroad_edits():
    assert suggest_alias("把结果发给新桥。", "明天我们再讨论。") is None
    assert suggest_alias("把结果发给新桥。", "把结果发给新桥！") is None
    assert suggest_alias("发给新桥", "发给负责的新桥") is None
    assert suggest_alias("数量填2", "数量填3") is None
    assert suggest_alias("三点找欣桥。", "五点找新桥。") is None
    assert suggest_alias("洗", "席") is None


def test_confirmation_returns_edited_values(qapp_offscreen):
    from PySide6.QtCore import QTimer

    def confirm():
        dialog = qapp_offscreen.activeModalWidget()
        dialog.alias_edit.setText("欣桥")
        dialog.term_edit.setText("新桥")
        dialog.save_button.click()

    QTimer.singleShot(0, confirm)
    assert confirm_alias("新乔", "新桥") == ("欣桥", "新桥")


def test_confirmation_can_be_cancelled(qapp_offscreen):
    from PySide6.QtCore import QTimer

    QTimer.singleShot(0, lambda: qapp_offscreen.activeModalWidget().reject())
    assert confirm_alias("欣桥", "新桥") is None


def test_manual_confirmation_prefills_selected_word_and_waits_for_wrong_spelling(qapp_offscreen):
    from PySide6.QtCore import QTimer

    checked = []

    def confirm():
        dialog = qapp_offscreen.activeModalWidget()
        checked.append((*dialog.values(), dialog.save_button.isEnabled()))
        dialog.alias_edit.setText("清样桥")
        dialog.save_button.click()

    QTimer.singleShot(0, confirm)
    assert confirm_alias("", "", selected="青砚桥") == ("清样桥", "青砚桥")
    assert checked == [("", "青砚桥", False)]


def test_concept_dialog_collapses_context_and_exposes_close_action(qapp_offscreen):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QPushButton, QWidget

    dialog = LearnDialog("清样桥", "青砚桥", "请把清样桥标成待复核。", "青砚桥")
    context = dialog.findChild(QWidget, "learnContext")
    toggle = dialog.findChild(QPushButton, "learnContextToggle")
    dialog.show()
    qapp_offscreen.processEvents()
    collapsed = dialog.height()
    assert dialog.windowFlags() & Qt.WindowType.FramelessWindowHint
    assert context.isHidden() and dialog.save_button.isEnabled()
    toggle.click()
    qapp_offscreen.processEvents()
    assert context.isVisible() and dialog.height() > collapsed
    toggle.click()
    qapp_offscreen.processEvents()
    assert context.isHidden() and dialog.height() == collapsed
    dialog.findChild(QPushButton, "learnClose").click()
    assert dialog.result() == 0


def test_reveal_restores_native_visibility_when_qt_thinks_dialog_is_visible(monkeypatch):
    from types import SimpleNamespace

    from PySide6.QtWidgets import QApplication
    from scribe import learn_dialog

    events = []
    dialog = SimpleNamespace(isVisible=lambda: True, winId=lambda: 42,
                             raise_=lambda: events.append("raised"),
                             activateWindow=lambda: events.append("activated"))
    user32 = SimpleNamespace(
        IsWindowVisible=lambda hwnd: "shown" in events,
        ShowWindow=lambda hwnd, action: events.append("shown"),
    )
    monkeypatch.setattr(QApplication, "platformName", staticmethod(lambda: "windows"))
    monkeypatch.setattr(learn_dialog.ctypes, "windll", SimpleNamespace(user32=user32))
    assert reveal_learning_dialog(dialog)
    assert events == ["shown", "raised", "activated"]
