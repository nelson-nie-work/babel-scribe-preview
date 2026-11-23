from PySide6.QtWidgets import QPushButton

from scribe import settings
from scribe.general_page import GeneralPage


def make_page(prefs, saved):
    page = GeneralPage(lambda: prefs, lambda k, v: (saved.__setitem__(k, v), prefs.__setitem__(k, v)),
                       lambda: [], lambda: False, lambda: True)
    page.sync()
    return page


def test_general_page_translate_controls(qapp_offscreen):
    prefs, saved = {**settings.DEFAULTS}, {}
    page = make_page(prefs, saved)
    switch = page.controls["selection_translate"]
    assert not switch.isChecked()   # 默认关闭, 需要时自己打开
    switch.click()
    assert saved["selection_translate"] is True
    page.excluded_input.setText("Photoshop.exe")
    page.excluded_add.click()
    assert saved["translate_excluded"] == ["photoshop.exe"]
    assert page.excluded_input.text() == ""


def test_excluded_add_ignores_empty_and_duplicates(qapp_offscreen):
    prefs, saved = {**settings.DEFAULTS, "translate_excluded": ["a.exe"]}, {}
    page = make_page(prefs, saved)
    for text in ("  ", "A.EXE"):
        page.excluded_input.setText(text)
        page.excluded_add.click()
    assert saved == {}


def test_excluded_remove_button_per_entry(qapp_offscreen):
    prefs, saved = {**settings.DEFAULTS, "translate_excluded": ["a.exe", "b.exe"]}, {}
    page = make_page(prefs, saved)
    removers = [b for b in page.findChildren(QPushButton) if b.accessibleName().startswith("移除 ")]
    assert [b.accessibleName() for b in removers] == ["移除 a.exe", "移除 b.exe"]
    removers[0].click()
    assert saved["translate_excluded"] == ["b.exe"]


def test_excluded_add_appends_exe_when_no_extension(qapp_offscreen):
    prefs, saved = {**settings.DEFAULTS, "translate_excluded": ["a.exe"]}, {}
    page = make_page(prefs, saved)
    page.excluded_input.setText("  Photoshop ")
    page.excluded_add.click()
    assert saved["translate_excluded"] == ["a.exe", "photoshop.exe"]
    saved.clear()
    page.excluded_input.setText("A")   # 补上 .exe 后与已有项重复
    page.excluded_add.click()
    assert saved == {}
