"""启动入口: 单实例 → Qt → 托盘/胶囊 → 麦克风 → 后台加载两个识别引擎 → 全局热键。"""
import concurrent.futures
import ctypes
import os
import sqlite3
import sys
import threading
import time

from PySide6.QtCore import QObject, QTimer, Signal, Slot
from PySide6.QtWidgets import QApplication

from scribe import keys, settings, single_instance, vocab
from scribe.asr.sensevoice import SenseVoiceEngine
from scribe.asr.whisper import WhisperEngine
from scribe.audio import MicStream, Recorder, input_devices
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
from scribe.llm import LlmClient
from scribe.output import (WinClipboard, WinKeys, deliver, foreground_elevated, foreground_window,
                           prepare_modifiers, read_accessible_selection, read_selection, window_application)
from scribe.paths import installed_launcher, logs_dir, settings_dir, shared_data_dir
from scribe.pipeline import Pipeline
from scribe.preview import LivePreview
from scribe.tray import Tray, format_stats, make_icon
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


def main() -> int:
    if not single_instance.acquire():
        return 0
    # 模型默认从 Hugging Face 下载。使用者可在环境中指定其他下载源。
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
    if sys.stderr is None:
        # pythonw.exe has no stream for the model downloader's progress bars.
        os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"
        _file_log("model download progress disabled for no-console launch")
    if installed_launcher():
        os.environ.setdefault("HF_HOME", str(settings_dir().parent / "Model Cache"))

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
    providers = keys.load_providers(data_dir / "keys.json")
    prefs = settings.load(polish_available=bool(providers))
    try:
        words = VocabularyStore(data_dir, vocab.load_terms(data_dir))
    except Exception as e:
        _file_log("vocab load error: %s" % type(e).__name__)
        words = VocabularyStore(data_dir, [])
    llm = LlmClient(providers)
    _file_log("llm mode %s, providers %s" % (prefs["llm_mode"], ",".join(sorted(providers)) or "none"))

    whisper, sensevoice = WhisperEngine(), SenseVoiceEngine()
    models = {"sensevoice": sensevoice, "whisper": whisper}
    operations = {}
    model_paused = False
    last_active = time.monotonic()

    def model_available() -> bool:
        return not model_paused and not any(op == "unload" for op in operations.values())

    engine = Pipeline(whisper, sensevoice, llm, mode=lambda: prefs["llm_mode"],
                      log=_file_log, vocabulary=lambda: words.current,
                      dual=lambda: prefs["dual_engine"], smart=lambda: prefs["smart_polish"],
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

        def target_ok() -> bool:
            return foreground_window() == target

        def show_suggestion(term: str) -> None:
            if ticket.is_set() or learning or not controller.can_learn or not target_ok():
                return
            if any(row["term"].casefold() == term.casefold() for row in words.current.terms):
                return
            _file_log("learn: edit suggestion")

            def remember() -> None:
                if not ticket.is_set() and controller.can_learn and target_ok():
                    save_word(term, "learned")

            word_capsule.suggest(term, remember)

        def detect() -> None:
            term = watch_edited_word(target, text, ticket, target_ok, _file_log)
            if term:
                post(lambda: show_suggestion(term))

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
            threading.Thread(target=llm.prewarm, args=(prefs["llm_mode"],), daemon=True,
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
            change_mic(value)
        try:
            updated = {**prefs, key: value}
            settings.save(updated)
        except OSError:
            if key == "microphone":
                change_mic(old)
            raise
        prefs = updated
        if key == "llm_mode":
            tray.set_mode(value)
            _file_log("llm mode -> %s" % value)
        if key == "idle_release" and not value:
            model_paused = False
        if key == "dual_engine" and value:
            whisper.failed = False
        if key in ("dual_engine", "hotkey", "idle_release"):
            ensure_models()
            update_status()

    def on_mode(mode: str) -> None:
        try:
            update_preference("llm_mode", mode)
        except OSError as e:
            _file_log("settings save error: %s" % type(e).__name__)
            tray.set_mode(prefs["llm_mode"])
            tray.showMessage("BABEL Scribe", "整理服务设置未保存")
        if general_page is not None:
            general_page.sync()

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
            general_page = GeneralPage(lambda: prefs, update_preference,
                                       lambda: [name for name in mic_choices() if name], startup_enabled,
                                       lambda: controller.can_learn and not learning)
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

    def copy_recent(text: str) -> None:
        try:
            clip.set_text(text)
        except OSError:
            tray.showMessage("BABEL Scribe", "复制失败，请从听写历史重试")
        else:
            tray.showMessage("BABEL Scribe", "已复制最终文字")

    tray = Tray(on_quit=app.quit, on_mode=on_mode, mode=prefs["llm_mode"],
                stats=lambda: format_stats(llm.recent_ms("openai"), llm.recent_ms("deepseek")),
                on_main=open_main_window, recent=recent_history, copy_recent=copy_recent)

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
        if not learning and (action is Action.START or recorder.recording):
            controller.handle(action)

    def on_level(level: float) -> None:
        if capsule.state == "listening":
            capsule.push_level(level)

    def mic_choices():
        try:
            return input_devices()
        except Exception as exc:
            _file_log("mic list error: %s" % type(exc).__name__)
            return {"": None}

    def change_mic(name: str) -> None:
        nonlocal mic
        devices = mic_choices()
        if name not in devices:
            raise ValueError("这支麦克风当前不可用，请重新连接或选系统默认。")
        previous = mic
        candidate = MicStream(recorder, on_level=on_level, device=devices[name])
        try:
            previous.stop()
            recorder.clear_preroll()
            candidate.start()
        except Exception as exc:
            _file_log("mic change error: %s" % type(exc).__name__)
            try:
                previous.start()
            except Exception as recovery:
                _file_log("mic restore error: %s" % type(recovery).__name__)
            raise ValueError("无法启用这支麦克风，请选其他设备。") from exc
        mic = candidate

    mic = MicStream(recorder, on_level=on_level)
    if prefs["microphone"]:
        devices = mic_choices()
        mic.device = devices.get(prefs["microphone"])
    try:
        mic.start()
    except Exception as e:
        _file_log("mic error: %r" % (e,))
        capsule.error("未检测到麦克风")
        window.refresh()

    def update_status() -> None:
        if model_paused:
            tray.set_status("已释放模型 · 下次听写时加载")
        elif engine.failed:
            tray.set_status("模型加载失败")
        elif engine.ready:
            key = settings.HOTKEYS[prefs["hotkey"]][0]
            suffix = "" if prefs["dual_engine"] and whisper.ready and sensevoice.ready else "（单引擎）"
            tray.set_status(f"就绪{suffix} · 按住 {key} 说话")
            if tray.isVisible():
                _file_log(f"tray ready: {tray.toolTip()}")
        else:
            tray.set_status("加载模型中…")
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
            finally:
                post(done)

        threading.Thread(target=run, daemon=True, name=f"scribe-{operation}-{name}").start()

    def ensure_models() -> None:
        if model_paused:
            return
        for name, eng in models.items():
            if name in operations:
                continue
            if name == "whisper" and not prefs["dual_engine"]:
                if eng.ready:
                    schedule_model(name, "unload")
            elif not eng.ready and not eng.failed:
                schedule_model(name, "load")

    def check_idle() -> None:
        nonlocal model_paused, last_active
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
    mic.stop()
    worker.shutdown(wait=False)
    edit_worker.shutdown(wait=False, cancel_futures=True)
    tray.hide()
    return rc
