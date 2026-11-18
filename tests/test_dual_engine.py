import threading
from types import SimpleNamespace

import pytest

from scribe.download import Cancelled, DownloadFailed, Progress
from scribe.dual_engine import DualEngineSetup, Phase, active
from scribe.gpu_check import Gpu
from scribe.pipeline import Pipeline
from tests.test_pipeline import FakeEngine, FakeLlm

GiB = 1024 ** 3
GOOD_GPU = Gpu("RTX", 16 * GiB, (591, 44), "591.44")


def _raise(error):
    def fn(*args, **kwargs):
        raise error
    return fn


@pytest.fixture
def calls():
    return []


@pytest.fixture
def make_setup(tmp_path, calls):
    def make(*, detect=lambda: GOOD_GPU, free_disk=lambda: 100 * GiB, ready=False, system_runtime=False,
             install=None, ensure=None, spawn=lambda fn: fn()):
        state = {"runtime": ready, "model": ready}
        runtime_root = tmp_path / "nvidia"
        whisper_dir = tmp_path / "models" / "whisper"

        def do_install(*, on_progress, cancel=None):
            calls.append("runtime.install")
            if install:
                install()
            on_progress(Progress(5, 10, 1.0, "files.pythonhosted.org"))
            runtime_root.mkdir(parents=True, exist_ok=True)
            (runtime_root / "x.dll").write_bytes(b"12345")
            state["runtime"] = True

        def do_ensure(model, *, on_progress, cancel=None):
            calls.append(f"models.ensure:{model.name}")
            if ensure:
                ensure()
            on_progress(Progress(3, 4, 1.0, "huggingface.co"))
            whisper_dir.mkdir(parents=True, exist_ok=True)
            (whisper_dir / "model.bin").write_bytes(b"abc")
            state["model"] = True
            return whisper_dir

        def do_remove_runtime():
            calls.append("runtime.remove")
            state["runtime"] = False

        def do_remove_model(model):
            calls.append(f"models.remove:{model.name}")
            state["model"] = False

        runtime = SimpleNamespace(
            installed=lambda: state["runtime"], system_runtime=lambda: system_runtime,
            missing_bytes=lambda: 0 if state["runtime"] else 1000, install=do_install,
            remove=do_remove_runtime, root=lambda: runtime_root)
        whisper = SimpleNamespace(name="whisper")
        models = SimpleNamespace(
            WHISPER=whisper, ready=lambda model: state["model"],
            missing_bytes=lambda model: 0 if state["model"] else 500, ensure=do_ensure,
            remove=do_remove_model, model_dir=lambda model: whisper_dir)
        seen = []
        extra = {} if spawn is None else {"spawn": spawn}
        setup = DualEngineSetup(on_change=seen.append, detect=detect, free_disk=free_disk,
                                runtime=runtime, models=models, **extra)
        setup.seen = seen
        return setup
    return make


def test_initial_state_follows_files(make_setup):
    assert make_setup(ready=False).state.phase is Phase.IDLE
    assert make_setup(ready=True).state.phase is Phase.READY


def test_start_refuses_without_gpu(make_setup):
    s = make_setup(detect=lambda: None)
    assert s.start() == "no_gpu"
    assert s.state.phase is Phase.FAILED and s.state.reason == "no_gpu"


def test_start_refuses_when_disk_is_low(make_setup):
    assert make_setup(free_disk=lambda: GiB).start() == "low_disk"


def test_low_disk_ignored_when_files_ready(make_setup):
    s = make_setup(ready=True, free_disk=lambda: 0)
    assert s.start() is None and s.state.phase is Phase.READY


def test_downloads_runtime_then_model_and_becomes_ready(make_setup, calls):
    s = make_setup()
    assert s.start() is None
    assert calls == ["runtime.install", "models.ensure:whisper"]
    assert s.state.phase is Phase.READY and s.ready
    assert s.state.files_bytes == 8
    phases = [st.phase for st in s.seen]
    assert phases[0] is Phase.DOWNLOADING and phases[-1] is Phase.READY
    assert any(st.runtime is not None and st.runtime.done == 5 for st in s.seen)
    assert any(st.model is not None and st.model.done == 3 for st in s.seen)


def test_system_runtime_skips_nvidia_download(make_setup, calls):
    s = make_setup(system_runtime=True)
    s.start()
    assert calls == ["models.ensure:whisper"]
    assert s.state.runtime.done == s.state.runtime.total


def test_download_bytes(make_setup):
    assert make_setup().download_bytes() == 1500
    assert make_setup(system_runtime=True).download_bytes() == 500
    assert make_setup(ready=True).download_bytes() == 0


def test_download_failure_reports_download(make_setup):
    s = make_setup(install=_raise(DownloadFailed()))
    s.start()
    assert (s.state.phase, s.state.reason) == (Phase.FAILED, "download")


def test_unexpected_error_reports_download(make_setup):
    s = make_setup(ensure=_raise(OSError("disk")))
    s.start()
    assert (s.state.phase, s.state.reason) == (Phase.FAILED, "download")


def test_cancel_returns_to_idle(make_setup):
    s = make_setup(ensure=_raise(Cancelled()))
    s.start()
    assert s.state.phase is Phase.IDLE


def test_cancel_sets_event_seen_by_download(make_setup):
    seen_cancel = []
    holder = {}

    def spawn(fn):
        holder["fn"] = fn

    s = make_setup(spawn=spawn)
    s.start()
    assert s.state.phase is Phase.DOWNLOADING
    original = s._runtime.install

    def install(*, on_progress, cancel=None):
        seen_cancel.append(cancel.is_set())
        raise Cancelled()

    s._runtime.install = install
    s.cancel()
    holder["fn"]()
    assert seen_cancel == [True] and s.state.phase is Phase.IDLE
    s._runtime.install = original


def test_mark_load_failed(make_setup):
    s = make_setup(ready=True)
    s.mark_load_failed()
    assert (s.state.phase, s.state.reason) == (Phase.FAILED, "load")


def test_remove_files_clears_ready(make_setup, calls):
    s = make_setup(ready=True)
    s.remove_files()
    assert calls[-2:] == ["runtime.remove", "models.remove:whisper"]
    assert s.state.phase is Phase.IDLE and not s.ready and s.state.files_bytes == 0


def test_remove_files_refused_while_downloading(make_setup):
    s = make_setup(spawn=lambda fn: None)
    s.start()
    with pytest.raises(RuntimeError):
        s.remove_files()


def test_start_while_downloading_does_not_spawn_twice(make_setup):
    spawned = []
    s = make_setup(spawn=spawned.append)
    s.start()
    s.start()
    assert len(spawned) == 1


def test_default_spawn_runs_in_named_daemon_thread(make_setup):
    names = []
    done = threading.Event()
    s = make_setup(spawn=None)
    s._spawn(lambda: (names.append((threading.current_thread().name, threading.current_thread().daemon)),
                      done.set()))
    done.wait(2)
    assert names == [("scribe-dual-setup", True)]


def test_active_requires_pref_and_files(make_setup):
    assert active(True, make_setup(ready=True))
    assert not active(False, make_setup(ready=True))
    assert not active(True, make_setup(ready=False))


def test_single_engine_keeps_working_while_preparing(make_setup):
    s = make_setup(ready=False)
    p = Pipeline(FakeEngine(ready=False), FakeEngine(ready=True), FakeLlm(), dual=lambda: active(True, s))
    assert p.ready


def test_reenable_while_cancel_pending_keeps_downloading(make_setup):
    holder = {}
    s = make_setup(spawn=lambda fn: holder.setdefault("fn", fn))
    s.start()
    s.cancel()
    assert s.start() is None              # 用户关了又马上打开，下载线程还没退出
    seen = []
    original = s._runtime.install

    def install(*, on_progress, cancel=None):
        seen.append(cancel.is_set())
        original(on_progress=on_progress, cancel=cancel)

    s._runtime.install = install
    holder["fn"]()
    assert seen == [False] and s.state.phase is Phase.READY


def test_reenable_after_thread_saw_cancel_restarts(make_setup, calls):
    holder = {}
    s = make_setup(spawn=lambda fn: holder.setdefault("fn", fn))
    s.start()
    original = s._runtime.install
    attempts = []

    def install(*, on_progress, cancel=None):
        attempts.append(1)
        if len(attempts) == 1:
            s.cancel()
            s.start()                     # 线程已看到取消、尚未退出时用户又打开
            raise Cancelled()
        original(on_progress=on_progress, cancel=cancel)

    s._runtime.install = install
    holder["fn"]()
    assert len(attempts) == 2 and s.state.phase is Phase.READY
