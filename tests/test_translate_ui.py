from PySide6.QtCore import Qt
from PySide6.QtWidgets import QPushButton

from scribe.translate import Translation
from scribe.translate_ui import TranslateBar, TranslateCard, TranslateToast


def button_texts(w):
    out = []
    for b in w.findChildren(QPushButton):
        if b.isHidden() or b.text() == "✕":
            continue
        t = b.text().strip()
        for suffix in (" Esc", " ↵"):
            t = t.removesuffix(suffix)
        out.append(t)
    return out


def click(w, text):
    for b in w.findChildren(QPushButton):
        if b.text() == text:
            b.click()
            return
    raise AssertionError(text)


def test_card_result_editable_has_replace(qapp_offscreen):
    c = TranslateCard()
    c.show_result(Translation("你好", "智谱", 600, False, False), "zh", editable=True)
    assert c.can_replace and "替换原文" in button_texts(c) and "智谱 0.6 秒" in c.header_text()


def test_card_result_readonly_only_copy(qapp_offscreen):
    c = TranslateCard()
    c.show_result(Translation("hi", "智谱", 600, False, False), "en", editable=False)
    assert not c.can_replace and button_texts(c) == ["关闭", "复制"]


def test_card_notes_unchanged_and_truncated(qapp_offscreen):
    c = TranslateCard()
    c.show_result(Translation("Whisper", "智谱", 600, True, True), "en", editable=False)
    assert "原文已经是英文" in c.note_text() and "前 2000 字" in c.note_text()
    c.show_result(Translation("你好", "智谱", 600, False, True), "zh", editable=False)
    assert "原文已经是中文" in c.note_text()


def test_card_error_and_no_service(qapp_offscreen):
    c = TranslateCard()
    c.show_error(["智谱", "DeepSeek"])
    assert "智谱、DeepSeek 都没有回应" in c.body_text() and "重试" in button_texts(c)
    c.show_no_service()
    assert "设置 → 整理服务" in c.body_text()


def test_card_loading_source_and_cancel(qapp_offscreen):
    c = TranslateCard()
    c.show_loading(10, 10, "zh", "hello")
    assert "正在翻译" in c.body_text() and "原文：hello" in c.note_text()
    assert button_texts(c) == ["取消"] and c.showing and not c.can_replace
    c.show_loading(10, 10, "zh", "x" * 50)
    assert "原文 50 字" in c.note_text()


def test_card_text_is_plain(qapp_offscreen):
    c = TranslateCard()
    c.show_result(Translation("<b>hi</b>", "智谱", 600, False, False), "en", editable=False)
    assert c.body_text() == "<b>hi</b>"


def test_card_signals(qapp_offscreen):
    c = TranslateCard()
    got = []
    c.replace_requested.connect(lambda: got.append("r"))
    c.copy_requested.connect(lambda: got.append("c"))
    c.retry_requested.connect(lambda: got.append("t"))
    c.closed.connect(lambda: got.append("x"))
    c.show_result(Translation("你好", "智谱", 600, False, False), "zh", editable=True)
    click(c, "替换原文 ↵"); click(c, "复制"); click(c, "关闭 Esc")
    c.show_error(["a"]); click(c, "重试")
    assert got == ["r", "c", "x", "t"]


def test_bar_emits_choice_and_first_time_hint(qapp_offscreen):
    b = TranslateBar()
    got = []
    b.chosen.connect(got.append)
    b.show_at(10, 10, first_time=True)
    assert "托盘关闭" in b.hint_text()
    click(b, "English")
    assert got == ["en"]
    b.show_at(10, 10, first_time=False)
    assert b.hint_text() == ""


def test_windows_do_not_take_focus(qapp_offscreen):
    for w in (TranslateBar(), TranslateCard(), TranslateToast()):
        assert w.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus
        assert w.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)


def test_toast_flash_shows_text(qapp_offscreen):
    t = TranslateToast()
    t.flash("已复制", 20, 20)
    assert t.isVisible() and t.text() == "已复制"


def test_card_x_closes_in_every_state(qapp_offscreen):
    c = TranslateCard()
    got = []
    c.closed.connect(lambda: got.append("x"))
    states = [lambda: c.show_loading(10, 10, "zh", "hi"),
              lambda: c.show_result(Translation("a", "智谱", 600, False, False), "zh", editable=True),
              lambda: c.show_error(["a"]), c.show_no_service]
    for show in states:
        show()
        x = next(b for b in c.findChildren(QPushButton) if b.text() == "✕")
        assert not x.isHidden() and x.focusPolicy() == Qt.FocusPolicy.NoFocus
        x.click()
        assert not c.isVisible() and not c.can_replace
    assert got == ["x"] * 4


def test_card_long_text_is_capped_and_buttons_stay_inside(qapp_offscreen):
    from PySide6.QtGui import QGuiApplication
    c = TranslateCard()
    c.show_loading(10, 10, "zh", "x")
    c.show_result(Translation("很长的译文。" * 400, "智谱", 600, True, False), "zh", editable=True)
    cap = QGuiApplication.primaryScreen().availableGeometry().height() * 0.6
    assert c.height() <= cap
    for b in c.findChildren(QPushButton):
        if not b.isHidden():
            r = b.mapTo(c, b.rect().bottomRight())
            assert r.y() <= c.height() and c.rect().contains(b.mapTo(c, b.rect().center()))


import pytest

@pytest.mark.parametrize("chars", [300, 600, 2400])
def test_card_is_tall_enough_for_its_text(qapp_offscreen, chars):
    # 真机反馈「后半段丢了」: 卡片高度按过期的布局尺寸算, 比正文区还矮, 结尾和按钮被切掉
    c = TranslateCard()
    c.show_loading(10, 10, "zh", "x" * 600)
    c.show_result(Translation(("很长的译文。" * 500)[:chars], "OpenAI", 2200, False, False),
                  "zh", editable=True)
    header = c._header.sizeHint().height()
    buttons = max(b.sizeHint().height() for b in c.findChildren(QPushButton) if not b.isHidden())
    assert c.height() >= c._scroll.height() + header + buttons
