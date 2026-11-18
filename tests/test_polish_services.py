from PySide6.QtWidgets import QLabel, QLineEdit, QPushButton

from scribe.llm import ServiceStatus
from scribe.polish_services import ServiceDialog, ServiceList


def entry(sid, name="OpenAI", key="sk-test-0000", preset="openai"):
    return {"id": sid, "preset": preset, "name": name, "base_url": "https://api.openai.com/v1",
            "model": "gpt-5.4-mini", "api_key": key}


def noop(*_args, **_kwargs):
    return None


def row_texts(lst):
    return [" ".join(label.text() for label in row.findChildren(QLabel)) for row in lst.row_widgets]


def all_text(widget):
    return " ".join(label.text() for label in widget.findChildren(QLabel) if not label.isHidden())


def click_move_down(lst, index):
    lst.row_widgets[index].findChild(QPushButton, "moveDown").click()


def choose_preset(dialog, preset):
    dialog.preset_combo.setCurrentIndex(dialog.preset_combo.findData(preset))


def make_list(rows, **kw):
    args = dict(save=noop, delete=noop, reorder=noop, test=noop, status=lambda i: None)
    args.update(kw)
    return ServiceList(lambda: rows, **args)


def test_list_shows_order_and_status(qapp_offscreen):
    lst = make_list([entry("a", "OpenAI"), entry("b", "DeepSeek")],
                    status=lambda i: ServiceStatus("ok", 900) if i == "a" else None)
    texts = row_texts(lst)
    assert texts[0].startswith("1") and "OpenAI" in texts[0] and "已连接 0.9 秒" in texts[0]
    assert texts[1].startswith("2") and "未测试" in texts[1]


def test_empty_list_explains_what_to_do(qapp_offscreen):
    lst = make_list([])                  # 持有引用: 临时控件在读取子标签时被回收会访问已释放的内存
    assert "还没有整理服务" in all_text(lst)


def test_unreadable_store_shows_reenter_hint(qapp_offscreen):
    broken, fine = make_list([], unreadable=lambda: True), make_list([])
    assert "无法读取" in all_text(broken)
    assert "无法读取" not in all_text(fine)


def test_move_down_calls_reorder(qapp_offscreen):
    got = []
    lst = make_list([entry("a"), entry("b")], reorder=got.append)
    click_move_down(lst, 0)
    assert got == [["b", "a"]]


def test_dialog_fills_preset_and_hides_key_for_ollama(qapp_offscreen):
    d = ServiceDialog(None, test=noop)
    choose_preset(d, "siliconflow")
    assert d.values()["base_url"] == "https://api.siliconflow.cn/v1"
    assert d.values()["model"] == "Qwen/Qwen2.5-7B-Instruct" and d.values()["name"] == "硅基流动"
    choose_preset(d, "ollama")
    assert d.key_row.isHidden()
    choose_preset(d, "openai")
    assert not d.key_row.isHidden()


def test_dialog_keeps_user_edited_model_when_preset_changes(qapp_offscreen):
    d = ServiceDialog(None, test=noop)
    choose_preset(d, "openrouter")
    d.model_edit.setText("anthropic/claude-haiku")
    choose_preset(d, "siliconflow")
    assert d.values()["model"] == "anthropic/claude-haiku"


def test_dialog_shows_auth_failure_text(qapp_offscreen):
    d = ServiceDialog(entry("a"), test=lambda values, done: done(ServiceStatus("auth", None)))
    d.test_button.click()
    assert "服务商拒绝了这个密钥" in d.result_label.text()


def test_dialog_test_sends_current_values_with_id(qapp_offscreen):
    seen = []
    d = ServiceDialog(entry("a"), test=lambda values, done: seen.append(values))
    d.key_edit.setText("  sk-new ")
    d.test_button.click()
    assert seen[0]["id"] == "a" and seen[0]["api_key"] == "sk-new"


def test_masked_key_until_revealed(qapp_offscreen):
    d = ServiceDialog(entry("a", key="sk-abcdefgh3f9a"), test=noop)
    assert d.key_edit.echoMode() == QLineEdit.EchoMode.Password
    d.reveal_button.click()
    assert d.key_edit.echoMode() == QLineEdit.EchoMode.Normal


def test_general_page_embeds_service_list_instead_of_mode_combo(qapp_offscreen):
    from scribe import settings
    from scribe.general_page import GeneralPage
    lst = make_list([entry("a")])
    page = GeneralPage(lambda: dict(settings.DEFAULTS), noop, lambda: [], lambda: False, lambda: True, lst)
    page.sync()
    assert "llm_mode" not in page.controls
    assert lst.parent() is not None and "OpenAI" in " ".join(row_texts(lst))


def test_refresh_does_not_orphan_old_rows_as_windows(qapp_offscreen):
    lst = make_list([entry("a"), entry("b")])
    old = list(lst.row_widgets)
    lst.refresh()
    assert not any(row.isWindow() for row in old)


def _freed_without_gc(make):
    import gc
    import weakref
    widget = make()
    ref = weakref.ref(widget)
    gc.disable()
    try:
        del widget
        return ref() is None
    finally:
        gc.enable()


def test_service_list_has_no_reference_cycle(qapp_offscreen):
    assert _freed_without_gc(lambda: make_list([entry("a"), entry("b")]))


def test_service_dialog_has_no_reference_cycle(qapp_offscreen):
    assert _freed_without_gc(lambda: ServiceDialog(entry("a"), test=noop))


def test_dialog_refuses_to_save_without_required_key(qapp_offscreen):
    d = ServiceDialog(None, test=noop)
    choose_preset(d, "openai")
    d.key_edit.setText("")
    d.save_button.click()
    assert d.result() != d.DialogCode.Accepted
    assert "API 密钥" in d.result_label.text()


def test_incomplete_test_explains_missing_value(qapp_offscreen):
    d = ServiceDialog(None, test=lambda values, done: done(ServiceStatus("missing_key")))
    d.test_button.click()
    assert "还没填 API 密钥" in d.result_label.text()


def test_slow_status_is_marked(qapp_offscreen):
    from scribe.polish_services import status_text
    assert status_text(ServiceStatus("ok", 3400, slow=True)) == "已连接 3.4 秒 · 偏慢"
    assert status_text(ServiceStatus("missing_field")) == "未填地址或模型"


def test_custom_service_still_offers_a_key_field(qapp_offscreen):
    d = ServiceDialog(None, test=noop)
    choose_preset(d, "custom")
    assert not d.key_row.isHidden()
    d.url_edit.setText("http://localhost:1234/v1")
    d.model_edit.setText("qwen2.5-7b")
    d.save_button.click()
    assert d.result() == d.DialogCode.Accepted
