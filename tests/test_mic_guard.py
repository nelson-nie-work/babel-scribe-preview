import pytest

from scribe.mic_guard import MicGuard


class FakeStream:
    def __init__(self, index):
        self.index = index
        self.dead = False
        self.stopped = False

    def healthy(self):
        return not self.dead and not self.stopped

    def stop(self):
        self.stopped = True


class Devices:
    def __init__(self):
        self.available = {"": None, "USB Mic": 3, "Other Mic": 5}
        self.calls = []

    def __call__(self, refresh):
        self.calls.append(refresh)
        return dict(self.available)


class Opener:
    def __init__(self):
        self.fail_all = False
        self.fail = set()
        self.opened = []

    def __call__(self, index):
        if self.fail_all or index in self.fail:
            raise OSError("cannot open")
        self.opened.append(index)
        return FakeStream(index)


@pytest.fixture
def devices():
    return Devices()


@pytest.fixture
def open_stream():
    return Opener()


@pytest.fixture
def notes():
    return []


@pytest.fixture
def pref():
    return {"name": "USB Mic"}


@pytest.fixture
def guard(devices, open_stream, notes, pref):
    g = MicGuard(devices=devices, open_stream=open_stream, preferred=lambda: pref["name"], notify=notes.append)
    assert g.start()
    devices.calls.clear()
    return g


def test_start_opens_preferred(guard, open_stream):
    assert guard.current == "USB Mic" and guard.stream.index == 3


def test_start_falls_back_when_preferred_missing(devices, open_stream, notes, pref):
    devices.available = {"": None}
    g = MicGuard(devices=devices, open_stream=open_stream, preferred=lambda: pref["name"], notify=notes.append)
    assert g.start() and g.current == "" and g.stream.index is None
    assert notes == ["麦克风 USB Mic 已断开，改用系统默认"]


def test_start_without_preference_uses_default_silently(devices, open_stream, notes):
    g = MicGuard(devices=devices, open_stream=open_stream, preferred=lambda: "", notify=notes.append)
    assert g.start() and g.current == "" and notes == []


def test_healthy_stream_skips_device_query(guard, devices, open_stream):
    assert guard.before_record() and devices.calls == []
    assert open_stream.opened == [3]


def test_dead_preferred_falls_back_and_notifies_once(guard, devices, notes):
    devices.available = {"": None}
    guard.stream.dead = True
    assert guard.before_record() and guard.current == ""
    assert devices.calls == [True]
    guard.stream.dead = True
    assert guard.before_record()
    assert notes == ["麦克风 USB Mic 已断开，改用系统默认"]


def test_dead_stream_reopens_preferred_when_still_listed(guard, notes):
    old = guard.stream
    old.dead = True
    assert guard.before_record() and guard.current == "USB Mic"
    assert old.stopped and guard.stream is not old and notes == []


def test_preferred_returns_on_idle_check(guard, devices, notes):
    devices.available = {"": None}
    guard.stream.dead = True
    guard.before_record()
    devices.available = {"": None, "USB Mic": 7}
    guard.idle_check()
    assert guard.current == "USB Mic" and guard.stream.index == 7 and len(notes) == 1


def test_disconnect_after_return_notifies_again(guard, devices, notes):
    devices.available = {"": None}
    guard.stream.dead = True
    guard.before_record()
    devices.available = {"": None, "USB Mic": 7}
    guard.idle_check()
    devices.available = {"": None}
    guard.stream.dead = True
    guard.before_record()
    assert len(notes) == 2


def test_idle_check_does_nothing_on_preferred(guard, devices):
    guard.idle_check()
    assert devices.calls == []


def test_idle_check_without_preference_does_nothing(devices, open_stream, notes):
    g = MicGuard(devices=devices, open_stream=open_stream, preferred=lambda: "", notify=notes.append)
    g.start()
    devices.calls.clear()
    g.idle_check()
    assert devices.calls == []


def test_no_device_blocks_recording(guard, devices, open_stream):
    devices.available = {"": None}
    open_stream.fail_all = True
    guard.stream.dead = True
    assert guard.before_record() is False and guard.stream is None


def test_switch_to_available_device(guard, open_stream):
    old = guard.stream
    guard.switch("Other Mic")
    assert guard.current == "Other Mic" and guard.stream.index == 5 and old.stopped


def test_switch_to_missing_device_raises(guard, devices):
    with pytest.raises(ValueError, match="当前不可用"):
        guard.switch("Gone Mic")
    assert guard.current == "USB Mic" and not guard.stream.stopped


def test_switch_failure_restores_previous(guard, open_stream):
    open_stream.fail.add(5)
    with pytest.raises(ValueError, match="无法启用"):
        guard.switch("Other Mic")
    assert guard.current == "USB Mic" and guard.stream.index == 3 and guard.stream.healthy()
