"""启动入口: 单实例 → Qt → 托盘/胶囊 → 麦克风 → 后台加载两个识别引擎 → 全局热键。"""
import concurrent.futures
import ctypes
import sqlite3
import sys
import threading
import time
import uuid
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal, Slot
from PySide6.QtWidgets import QApplication

from scribe import dual_engine, gpu_check, keys, settings, single_instance, vocab
from scribe.asr.sensevoice import SenseVoiceEngine
from scribe.asr.whisper import WhisperEngine
from scribe.audio import MicStream, Recorder, input_devices
from scribe.dual_engine import DualEngineSetup, Phase, SetupState
from scribe.dual_engine_panel import DualEnginePanel
from scribe.capsule import CapsuleModel, CapsuleWindow
from scribe.config import APP_USER_MODEL_ID
from scribe.controller import Controller
from scribe.edit_watch import watch_edited_word
from scribe.general_page import GeneralPage
from scribe.history import HistoryStore
from scribe.history_page import HistoryPage
from scribe.hotkey import Action, HotkeyListener, HotkeyMachine
from scribe.learn_dialog import confirm_alias, reveal_learning_dialog
from scribe.learning import is_word_selection, suggest_alias
from scribe.llm import LlmClient, ServiceStatus
from scribe.mic_guard import MicGuard
from scribe.onboarding import Notices
from scribe.output import (WinClipboard, WinKeys, deliver, foreground_elevated, foreground_window,
                           prepare_modifiers, read_accessible_selection, read_selection, window_application)
from scribe.paths import installed_launcher, logs_dir, settings_dir, shared_data_dir
from scribe.pipeline import Pipeline
from scribe.polish_services import ServiceList
from scribe.preview import LivePreview
from scribe.provider_store import ProviderStore, migrate_order, store_path
from scribe.tray import Tray, format_download, format_gb, format_stats, make_icon
from scribe.vocab_store import VocabularyStore
from scribe.vocab_window import VocabularyWindow
from scribe.word_capsule import WordCapsule

_IDLE_RELEASE_S = 30 * 60


class _Bridge(QObject):
    """任意线程 emit(fn) → fn 在主线程执行(跨线程自动排队)。"""
    call = Signal(object)

    def __init__(self):
        super().__init__()
        self.call.connect(self.invoke)

    @Slot(object)
    def invoke(self, fn):
        fn()


def _file_log(line: str) -> None:
    try:
        with open(logs_dir() / "scribe.log", "a", encoding="utf-8") as f:
            f.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), line))
    except OSError:
        pass


def load_services(data_dir: Path, prefs: dict, store: ProviderStore) -> tuple:
    """读加密的服务列表, 导入旧 keys.json 里新出现的服务, 首次按老用户的模式排序。"""
    entries = store.load()
    merged = store.import_legacy(entries, Path(data_dir) / "keys.json")
    if not prefs.get("services_migrated"):
        merged = migrate_order(merged, prefs.get("llm_mode", "gpt"))
        prefs = {**prefs, "services_migrated": True}
    if merged != entries:
        store.save(merged)
    return merged, prefs


def service_confs(entries: list) -> list:
    return [conf for conf in (keys.conf_from_entry(e) for e in entries) if conf is not None]


def tray_status(*, paused: bool, sensevoice, engine, dual_pref: bool, setup_state: SetupState,
                whisper_ready: bool, hotkey_label: str) -> tuple[str, str]:
    """托盘状态与提示细节，按优先级取第一条。"""
    if paused:
        return "已释放模型 · 下次听写时加载", ""
    progress = sensevoice.progress
    if progress is not None and not sensevoice.ready:
        return "下载模型 " + format_download(progress), ("来源 " + progress.source if progress.source else "")
    if sensevoice.failed:
        return ("模型下载失败 · 点击重试", "") if sensevoice.error == "download" else ("模型加载失败", "")
    if engine.ready and setup_state.phase is Phase.DOWNLOADING:
        parts = [p for p in (setup_state.runtime, setup_state.model) if p is not None]
        total = sum(p.total for p in parts)
        percent = int(sum(p.done for p in parts) * 100 / total) if total else 0
        return f"就绪（单引擎）· 下载双引擎 {percent}%", ""
    if engine.ready:
        suffix = "" if dual_pref and whisper_ready else "（单引擎）"
        return f"就绪{suffix} · 按住 {hotkey_label} 说话", ""
    return "加载模型中…", ""


def main() -> int:
    if not single_instance.acquire():
        return 0
    if sys.platform == "win32":
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    app = QApplication(sys.argv)
    app.setWindowIcon(make_icon())
    app.setQuitOnLastWindowClosed(False)
    bridge = _Bridge()
    post = bridge.call.emit

    capsule = CapsuleModel()
    window = CapsuleWindow(capsule)
    word_capsule = WordCapsule()
    recorder = Recorder()

    # 本机设置、整理服务 key、个人词库;key 永不进日志
    history = HistoryStore(settings_dir() / "history.sqlite")
    data_dir = shared_data_dir()
    store = ProviderStore(store_path(data_dir))
    existed = settings.exists()          # 必须在第一次 load/save 之前
    try:
        entries, startup_prefs = load_services(data_dir, settings.load(), store)
    except OSError as e:
        _file_log("services load error: %s" % type(e).__name__)
        entries, startup_prefs = store.load(), settings.load()
    prefs = settings.load(polish_available=bool(service_confs(entries)))
    if startup_prefs["services_migrated"] and not prefs["services_migrated"]:
        prefs["services_migrated"] = True
        try:
            settings.save(prefs)
        except OSError as e:
            _file_log("settings save error: %s" % type(e).__name__)
    defaulted, enable_startup = settings.first_run_defaults(prefs, installed=installed_launcher() is not None,
                                                            existed=existed)
    if enable_startup:
        try:
            settings.set_startup(True)
        except OSError as e:
            _file_log("startup default error: %s" % type(e).__name__)
    if defaulted != prefs:
        prefs = defaulted
        try:
            settings.save(prefs)
        except OSError as e:
            _file_log("settings save error: %s" % type(e).__name__)
    try:
        words = VocabularyStore(data_dir, vocab.load_terms(data_dir))
    except Exception as e:
        _file_log("vocab load error: %s" % type(e).__name__)
        words = VocabularyStore(data_dir, [])
    llm = LlmClient(service_confs(entries))
    _file_log("llm services %d: %s" % (len(llm.providers), ",".join(c.name for c in llm.providers) or "none"))

    whisper = WhisperEngine()
    sensevoice = SenseVoiceEngine(on_progress=lambda p: post(update_status))
    models = {"sensevoice": sensevoice, "whisper": whisper}
    setup = DualEngineSetup(on_change=lambda state: post(lambda: on_setup(state)), log=_file_log)

    def dual_active() -> bool:
        return dual_engine.active(prefs["dual_engine"], setup)
    operations = {}
    model_paused = False
    last_active = time.monotonic()

    def model_available() -> bool:
        return not model_paused and not any(op == "unload" for op in operations.values())

    engine = Pipeline(whisper, sensevoice, llm,
                      log=_file_log, vocabulary=lambda: words.current,
                      dual=dual_active, smart=lambda: prefs["smart_polish"],
                      style=lambda: prefs["style"], available=model_available)
    clip, win_keys = WinClipboard(int(window.winId())), WinKeys()
    worker = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="scribe-asr")
    edit_worker = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="scribe-edit")
    edit_stop: threading.Event | None = None

    preview = LivePreview(sensevoice, recorder, on_text=lambda text: controller.on_preview(text))

    def stop_edit_watch() -> None:
        nonlocal edit_stop
        if edit_stop is not None:
            edit_stop.set()
            edit_stop = None

    def watch_output_edit(text: str, target: int) -> None:
        nonlocal edit_stop
        stop_edit_watch()
        if learning or not target:
            return
        ticket = edit_stop = threading.Event()
        known_terms = tuple(row["term"] for row in words.current.terms)

        def target_ok() -> bool:
            return foreground_window() == target

        def show_suggestion(alias: str, term: str) -> None:
            if ticket.is_set() or learning or not controller.can_learn or not target_ok():
                return
            if any(row["term"] == term and alias in row.get("aliases", ()) for row in words.current.terms):
                return
            _file_log("learn: edit suggestion")

            def remember() -> None:
                if not ticket.is_set() and controller.can_learn and target_ok():
                    save_correction(alias, term)

            word_capsule.suggest(alias, term, remember)

        def detect() -> None:
            pair = watch_edited_word(target, text, ticket, target_ok, _file_log,
                                     known_terms=known_terms)
            if pair:
                post(lambda: show_suggestion(*pair))

        edit_worker.submit(detect)

    def on_start() -> None:
        nonlocal model_paused, last_active
        stop_edit_watch()
        word_capsule.dismiss()
        last_active = time.monotonic()
        if model_paused:
            model_paused = False
            ensure_models()
            update_status()
        if prefs["smart_polish"]:
            threading.Thread(target=llm.prewarm, daemon=True,
                             name="scribe-prewarm").start()
        preview.start()

    def use_typing(app_id: str) -> bool:
        return app_id in prefs["typing_apps"]

    def set_typing(app_id: str, enabled: bool) -> None:
        apps = set(prefs["typing_apps"])
        if enabled:
            apps.add(app_id)
        else:
            apps.discard(app_id)
        update_preference("typing_apps", sorted(apps))

    def save_history(result, app_id: str, elapsed_ms: int, outcome: str) -> None:
        if outcome != "failed":
            post(notices.dictated)
        try:
            history.add(result, app_id, elapsed_ms, outcome)
        except sqlite3.Error as exc:
            _file_log("history save error: %s" % type(exc).__name__)
        else:
            post(refresh_history)

    controller = Controller(
        recorder, engine,
        output=lambda text, target: deliver(
            text, clip, win_keys, foreground_elevated, prepare=prepare_modifiers,
            target_ok=lambda: foreground_window() == target,
            force_typing=use_typing(window_application(target))),
        capsule=capsule, on_change=window.refresh,
        run_async=lambda fn: worker.submit(fn), post=post, log=_file_log,
        foreground=foreground_window, on_start=on_start, on_stop=preview.stop, on_output=watch_output_edit,
        identify=window_application, on_record=save_history)

    def update_preference(key: str, value) -> None:
        nonlocal prefs, model_paused
        if key == "startup":
            settings.set_startup(bool(value))
            return
        old = prefs[key]
        if old == value:
            return
        if key in ("hotkey", "microphone", "dual_engine") and not controller.can_learn:
            raise ValueError("等本次听写结束后再更改。")
        if key == "microphone":
            mic_guard.switch(value)
        if key == "dual_engine":
            if value:
                start_dual()
            else:
                setup.cancel()
        try:
            updated = {**prefs, key: value}
            settings.save(updated)
        except OSError:
            if key == "microphone":
                mic_guard.switch(old)
            raise
        if key == "smart_polish":
            updated["polish_user_off"] = not value
            settings.save(updated)
        prefs = updated
        if key == "idle_release" and not value:
            model_paused = False
        if key in ("dual_engine", "hotkey", "idle_release"):
            ensure_models()
            update_status()

    def save_pref(key: str, value) -> None:
        nonlocal prefs
        prefs = {**prefs, key: value}
        try:
            settings.save(prefs)
        except OSError as e:
            _file_log("settings save error: %s" % type(e).__name__)

    def start_dual() -> None:
        """检测显卡并开始准备双引擎文件；不能开启时抛出原因。"""
        whisper.failed = False
        reason = setup.start()
        if reason:
            _file_log("dual engine refused: %s" % reason)
            raise ValueError(gpu_check.REASONS[reason])

    def enable_dual() -> None:
        """开关之外的入口（重试按钮、点击通知）。"""
        if prefs["dual_engine"]:
            start_dual()
            ensure_models()
            update_status()
        else:
            update_preference("dual_engine", True)
        if general_page is not None:
            general_page.sync()

    def retry_dual() -> None:
        try:
            enable_dual()
        except ValueError:
            pass                    # 原因由面板显示
        if general_page is not None:
            general_page.sync()

    def delete_dual_files() -> None:
        if "whisper" in operations or whisper.ready:
            if general_page is not None:
                general_page._set_status("正在释放 Whisper，请稍后再试。", error=True)
            return
        try:
            setup.remove_files()
        except RuntimeError:
            return
        _file_log("dual engine files removed")
        if general_page is not None:
            general_page._set_status("已删除双引擎文件。")
            general_page.sync()

    last_setup_phase = setup.state.phase

    def on_setup(state: SetupState) -> None:
        nonlocal last_setup_phase
        changed = state.phase is not last_setup_phase
        last_setup_phase = state.phase
        if state.phase is Phase.FAILED and state.reason == "download" and prefs["dual_engine"]:
            save_pref("dual_engine", False)
        if state.phase is Phase.READY:
            ensure_models()
        update_status()
        if general_page is not None:
            if changed:
                general_page.sync()
            else:
                general_page.sync_dual()     # 下载进度：只刷新面板

    def services() -> list:
        return [dict(e) for e in entries]

    def services_changed(new_entries: list) -> None:
        nonlocal entries, prefs
        before = len(llm.providers)
        store.save(new_entries)
        entries = [dict(e) for e in new_entries]
        llm.set_providers(service_confs(entries))
        _file_log("llm services %d: %s" % (len(llm.providers), ",".join(c.name for c in llm.providers) or "none"))
        updated = settings.apply_service_change(prefs, before, len(llm.providers))
        if updated is not prefs:
            try:
                settings.save(updated)
                prefs = updated
            except OSError as e:
                _file_log("settings save error: %s" % type(e).__name__)
        if llm.providers and prefs["smart_polish"]:
            threading.Thread(target=llm.prewarm, daemon=True, name="scribe-prewarm").start()
        if general_page is not None:
            general_page.sync()

    def save_service(entry_id, values: dict) -> dict:
        fields = {k: values.get(k, "") for k in ("preset", "name", "base_url", "model", "api_key")}
        current = [dict(e) for e in entries]
        if entry_id is None:
            saved = {"id": uuid.uuid4().hex, **fields}
            current.append(saved)
        else:
            index = next(i for i, e in enumerate(current) if e["id"] == entry_id)
            saved = {**current[index], **fields}
            current[index] = saved
        services_changed(current)
        return dict(saved)

    def delete_service(entry_id: str) -> None:
        services_changed([e for e in entries if e["id"] != entry_id])

    def reorder_services(ids: list) -> None:
        by_id = {e["id"]: e for e in entries}
        ordered = [by_id[i] for i in ids if i in by_id]
        services_changed(ordered + [e for e in entries if e["id"] not in ids])

    def test_service(values: dict, done) -> None:
        missing = keys.missing_field(values)
        conf = None if missing else keys.conf_from_entry({**values, "id": values.get("id") or "new"})
        if conf is None:
            done(ServiceStatus(missing or "missing_field"))
            return

        saved = next((e for e in entries if e["id"] == values.get("id")), None)

        def run():
            result = llm.test(conf)
            if keys.same_connection(values, saved):      # 只有测的就是已保存的配置, 才更新列表里的状态
                llm.record_status(saved["id"], result)
            post(lambda: done(result))

        threading.Thread(target=run, daemon=True, name="scribe-test-service").start()

    def service_status(entry_id: str):
        entry = next((e for e in entries if e["id"] == entry_id), None)
        missing = keys.missing_field(entry) if entry else None
        return ServiceStatus(missing) if missing else llm.status(entry_id)

    def on_service(entry_id: str) -> None:
        """托盘里选了另一个服务: 把它移到第 1 位。"""
        try:
            reorder_services([entry_id] + [e["id"] for e in entries if e["id"] != entry_id])
        except OSError as e:
            _file_log("services save error: %s" % type(e).__name__)
            tray.showMessage("BABEL Scribe", "整理服务设置未保存")

    vocab_window: VocabularyWindow | None = None
    general_page: GeneralPage | None = None
    history_page: HistoryPage | None = None

    def reset_vocab_window() -> None:
        nonlocal vocab_window, general_page, history_page
        vocab_window = None
        general_page = None
        history_page = None

    def open_main_window() -> None:
        nonlocal vocab_window, general_page, history_page
        if vocab_window is None:
            service_list = ServiceList(services, save=save_service, delete=delete_service,
                                       reorder=reorder_services, test=test_service, status=service_status,
                                       unreadable=lambda: store.unreadable)
            dual_panel = DualEnginePanel(on_cancel=setup.cancel, on_retry=retry_dual, on_delete=delete_dual_files)
            general_page = GeneralPage(lambda: prefs, update_preference,
                                       lambda: [name for name in mic_choices() if name], startup_enabled,
                                       lambda: controller.can_learn and not learning, service_list,
                                       dual_panel=dual_panel, dual_state=lambda: setup.state)
            history_page = HistoryPage(history, copy=clip.set_text, learn=learn_history_word,
                                       typing=use_typing, set_typing=set_typing)
            vocab_window = VocabularyWindow(words, pages={"general": general_page, "history": history_page})
            vocab_window.destroyed.connect(reset_vocab_window)
        _file_log("main: window visible=%s page=%s" % (vocab_window.open_from_tray(), vocab_window.page))

    def refresh_vocab_window() -> None:
        if vocab_window is not None:
            vocab_window.refresh_from_store()

    def refresh_history() -> None:
        if vocab_window is not None and vocab_window.page == "history":
            history_page.refresh()

    def prune_history() -> None:
        try:
            history.prune()
        except sqlite3.Error as exc:
            _file_log("history prune error: %s" % type(exc).__name__)
        else:
            refresh_history()

    def learn_history_word(alias: str, term: str) -> bool:
        added = words.learn_alias(alias, term)
        refresh_vocab_window()
        return added

    def startup_enabled() -> bool:
        try:
            return settings.startup_enabled()
        except OSError as exc:
            _file_log("startup read error: %s" % type(exc).__name__)
            return False

    def recent_history():
        try:
            return [(record.text, record.app_name) for record in history.list(limit=3)]
        except sqlite3.Error as exc:
            _file_log("history recent error: %s" % type(exc).__name__)
            return []

    def retry_models() -> None:
        """托盘单击：模型下载失败后重试。"""
        if sensevoice.failed and sensevoice.error == "download":
            sensevoice.failed = False
            ensure_models()
            update_status()

    def copy_recent(text: str) -> None:
        try:
            clip.set_text(text)
        except OSError:
            tray.showMessage("BABEL Scribe", "复制失败，请从听写历史重试")
        else:
            tray.showMessage("BABEL Scribe", "已复制最终文字")

    tray = Tray(on_quit=app.quit,
                services=lambda: [(e["id"], e.get("name") or e.get("preset", "")) for e in entries],
                on_service=on_service,
                stats=lambda: format_stats([(c.label, llm.recent_ms(c.id)) for c in llm.providers]),
                on_main=open_main_window, recent=recent_history, copy_recent=copy_recent,
                on_click=retry_models)
    notices = Notices(lambda: prefs, save_pref)
    last_notice = ""
    dual_gpu = None
    dual_ok = False
    dual_checked = False

    def check_dual_offer() -> None:
        """第一次就绪后在后台算一次：这台电脑能否开双引擎。"""
        try:
            gpu = gpu_check.detect()
            ok = setup.check() is None and not setup.ready
        except Exception as exc:
            _file_log("dual check error: %s" % type(exc).__name__)
            return

        def apply() -> None:
            nonlocal dual_gpu, dual_ok
            dual_gpu, dual_ok = gpu, ok and gpu is not None
        post(apply)

    def show_notices() -> None:
        nonlocal last_notice, dual_checked
        if engine.ready and not dual_checked:
            dual_checked = True
            threading.Thread(target=check_dual_offer, daemon=True, name="scribe-dual-check").start()
        due = notices.due(ready=engine.ready, idle=controller.can_learn and not learning, dual_ok=dual_ok)
        if due == "ready":
            key = settings.HOTKEYS[prefs["hotkey"]][0]
            tray.showMessage("BABEL Scribe", f"BABEL Scribe 已就绪。按住 {key} 说话，松开就出字。")
        elif due == "dual" and dual_gpu is not None:
            tray.showMessage("可以开启更准的双引擎",
                             f"检测到 {dual_gpu.name}（{dual_gpu.vram / 1024 ** 3:.0f} GB）。"
                             f"双引擎会同时用 Whisper 识别，错字更少。"
                             f"首次需下载约 {format_gb(setup.download_bytes())}。点此开启。")
        if due:
            last_notice = due

    def on_message_clicked() -> None:
        if last_notice != "dual":
            return
        open_main_window()
        vocab_window.set_page("general")
        try:
            enable_dual()
        except ValueError:
            pass                    # 原因由面板显示
        if general_page is not None:
            general_page.sync()

    tray.messageClicked.connect(on_message_clicked)

    learning = False

    def learn_notice(message: str) -> None:
        tray.showMessage("BABEL Scribe · 学词", message)

    def save_word(term: str, source: str) -> None:
        try:
            entry = words.add_word(term, source=source)
        except ValueError as exc:
            learn_notice(str(exc))
            return
        except (OSError, TypeError, KeyError) as exc:
            _file_log("vocab save error: %s" % type(exc).__name__)
            learn_notice("词库保存失败，请检查词库文件")
            return
        if entry is None:
            refresh_vocab_window()
            word_capsule.notice(f"「{term}」已在词库")
            return
        refresh_vocab_window()
        _file_log("learn: word saved source=%s" % source)

        def undo() -> None:
            try:
                if words.undo_word(entry):
                    refresh_vocab_window()
                    _file_log("learn: word undone")
                    word_capsule.notice("已撤销收词")
                else:
                    learn_notice("词条已有其他更改，请在词库中核对")
            except (OSError, ValueError, TypeError, KeyError) as exc:
                _file_log("vocab undo error: %s" % type(exc).__name__)
                learn_notice("撤销未保存，请检查词库文件")

        word_capsule.saved(term, undo)

    def save_correction(alias: str, term: str) -> None:
        try:
            addition = words.add_alias(alias, term)
        except ValueError as exc:
            learn_notice(str(exc))
            return
        except (OSError, TypeError, KeyError) as exc:
            _file_log("vocab save error: %s" % type(exc).__name__)
            learn_notice("词库保存失败，请检查词库文件")
            return
        refresh_vocab_window()
        if addition is None:
            word_capsule.notice("这条纠错已在词库")
            return
        _file_log("learn: edit correction saved")

        def undo() -> None:
            try:
                if words.undo_alias(addition):
                    refresh_vocab_window()
                    _file_log("learn: edit correction undone")
                    word_capsule.notice("已撤销纠错")
                else:
                    learn_notice("纠错规则已有其他更改，请在词库中核对")
            except (OSError, ValueError, TypeError, KeyError) as exc:
                _file_log("vocab undo error: %s" % type(exc).__name__)
                learn_notice("撤销未保存，请检查词库文件")

        word_capsule.saved(f"{alias} → {term}", undo)

    def on_learn() -> None:
        nonlocal learning
        if learning:
            dialog = app.activeModalWidget()
            if dialog is not None:
                _file_log("learn: re-show visible=%s" % reveal_learning_dialog(dialog))
            return
        if not controller.can_learn:
            learn_notice("等这句话出字，再按右 Ctrl + L")
            return
        target = foreground_window()
        if not target:
            return
        original = controller.learn_source(target) or ""
        learning = True

        def target_ok() -> bool:
            return foreground_window() == target

        def show_candidate(selected: str | None, source: str) -> None:
            nonlocal learning
            try:
                if not target_ok():
                    _file_log("learn: target changed")
                    return
                if selected and is_word_selection(selected.strip()):
                    save_word(selected.strip(), "manual")
                    return
                terms = (row["term"] for row in words.current.terms)
                proposal = suggest_alias(original, selected, known_terms=terms) if selected else None
                _file_log("learn: selection=%s recent=%s suggested=%s" % (source, bool(original), bool(proposal)))
                confirmed = confirm_alias(
                    *(proposal or ("", "")), original=original, selected=selected or "",
                    on_shown=lambda visible: _file_log("learn: dialog visible=%s" % visible),
                )
                if confirmed is None:
                    _file_log("learn: cancelled")
                elif words.learn_alias(*confirmed):
                    refresh_vocab_window()
                    _file_log("learn: saved")
                    learn_notice("已加入词库，下次听写生效")
                else:
                    refresh_vocab_window()
                    _file_log("learn: already in vocab")
                    learn_notice("这个写法已在词库中")
            except ValueError as exc:
                learn_notice(str(exc))
            except (OSError, TypeError, KeyError) as exc:
                _file_log("vocab save error: %s" % type(exc).__name__)
                learn_notice("词库保存失败，请检查词库文件")
            finally:
                learning = False

        def capture() -> None:
            selected = None
            source = "none"
            try:
                selected = read_selection(clip, win_keys, target=target, target_ok=target_ok)
                if selected:
                    source = "clipboard"
                else:
                    selected = read_accessible_selection(target, target_ok)
                    if selected:
                        source = "accessibility"
            except OSError as exc:
                _file_log("learn selection error: %s" % type(exc).__name__)
            finally:
                post(lambda: show_candidate(selected, source))

        worker.submit(capture)

    def on_action(action: Action) -> None:
        if learning or not (action is Action.START or recorder.recording):
            return
        if action is Action.START and not recorder.recording and not mic_guard.before_record():
            capsule.error("未检测到麦克风")
            window.refresh()
            return
        controller.handle(action)

    def on_level(level: float) -> None:
        if capsule.state == "listening":
            capsule.push_level(level)

    def mic_choices(refresh: bool = False):
        try:
            return input_devices(refresh)
        except Exception as exc:
            _file_log("mic list error: %s" % type(exc).__name__)
            return {"": None}

    def open_mic(index):
        stream = MicStream(recorder, on_level=on_level, device=index)
        recorder.clear_preroll()
        stream.start()
        return stream

    mic_guard = MicGuard(devices=mic_choices, open_stream=open_mic, preferred=lambda: prefs["microphone"],
                         notify=lambda text: tray.showMessage("BABEL Scribe", text), log=_file_log)
    if not mic_guard.start():
        _file_log("mic error: no input device")
        capsule.error("未检测到麦克风")
        window.refresh()

    def update_status() -> None:
        text, detail = tray_status(paused=model_paused, sensevoice=sensevoice, engine=engine,
                                   dual_pref=prefs["dual_engine"], setup_state=setup.state,
                                   whisper_ready=whisper.ready, hotkey_label=settings.HOTKEYS[prefs["hotkey"]][0])
        previous = tray.toolTip()
        tray.set_status(text, detail)
        if text.startswith("就绪") and tray.isVisible() and tray.toolTip() != previous:
            _file_log(f"tray ready: {tray.toolTip()}")
        controller.tick()
        window.refresh()

    def schedule_model(name: str, operation: str) -> None:
        operations[name] = operation
        eng = models[name]

        def done():
            operations.pop(name)
            ensure_models()
            update_status()

        def run():
            started = time.monotonic()
            try:
                if operation == "load":
                    eng.load()
                    _file_log("%s ready in %.1f s" % (name, time.monotonic() - started))
                else:
                    eng.unload()
                    _file_log("%s released" % name)
            except Exception as exc:
                _file_log("%s %s error: %s" % (name, operation, type(exc).__name__))
                if name == "whisper" and operation == "load":
                    post(setup.mark_load_failed)
            finally:
                post(done)

        threading.Thread(target=run, daemon=True, name=f"scribe-{operation}-{name}").start()

    def ensure_models() -> None:
        if model_paused:
            return
        for name, eng in models.items():
            if name in operations:
                continue
            if name == "whisper" and not dual_active():
                if eng.ready:
                    schedule_model(name, "unload")
            elif not eng.ready and not eng.failed:
                schedule_model(name, "load")

    idle_ticks = 0

    def check_idle() -> None:
        nonlocal model_paused, last_active, idle_ticks
        idle_ticks += 1
        show_notices()
        if idle_ticks % 5 == 0 and controller.can_learn and not learning:
            mic_guard.idle_check()
        now = time.monotonic()
        if not controller.can_learn:
            last_active = now
        elif (prefs["idle_release"] and not model_paused and not operations
              and engine.ready and now - last_active >= _IDLE_RELEASE_S):
            model_paused = True
            for name, eng in models.items():
                if eng.ready:
                    schedule_model(name, "unload")
            update_status()

    if prefs["dual_engine"] and not setup.ready:
        reason = setup.start()
        if reason:
            _file_log("dual engine preference turned off at startup: %s" % reason)
            save_pref("dual_engine", False)
    ensure_models()

    hotkey = HotkeyListener(HotkeyMachine(), emit=lambda action: post(lambda: on_action(action)),
                            on_learn=lambda: post(on_learn),
                            trigger=lambda: settings.HOTKEYS[prefs["hotkey"]][1],
                            enabled=lambda: general_page is None or not general_page.capturing)
    hotkey.start()

    ticker = QTimer(interval=50, timeout=controller.tick)
    ticker.start()
    idle_ticker = QTimer(interval=1000, timeout=check_idle)
    idle_ticker.start()
    history_ticker = QTimer(interval=60 * 60 * 1000, timeout=prune_history)
    history_ticker.start()
    QTimer.singleShot(0, prune_history)

    _file_log("started")
    rc = app.exec()
    preview.stop()
    stop_edit_watch()
    word_capsule.dismiss()
    hotkey.stop()
    if mic_guard.stream is not None:
        mic_guard.stream.stop()
    worker.shutdown(wait=False)
    edit_worker.shutdown(wait=False, cancel_futures=True)
    tray.hide()
    return rc
