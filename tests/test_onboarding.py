import pytest

from scribe import settings
from scribe.onboarding import READY_GAP_S, Notices


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def prefs():
    return dict(settings.DEFAULTS)


@pytest.fixture
def notices(prefs, clock):
    return Notices(lambda: prefs, prefs.__setitem__, clock)


def test_ready_notice_once_and_only_when_idle(notices, prefs):
    assert notices.due(ready=True, idle=False, dual_ok=False) is None
    assert notices.due(ready=False, idle=True, dual_ok=False) is None
    assert notices.due(ready=True, idle=True, dual_ok=False) == "ready"
    assert prefs["ready_noticed"] is True
    assert notices.due(ready=True, idle=True, dual_ok=False) is None


def test_dual_prompt_waits_for_dictation_after_ready(notices, prefs, clock):
    assert notices.due(ready=True, idle=True, dual_ok=True) == "ready"
    assert notices.due(ready=True, idle=True, dual_ok=True) is None
    clock.advance(READY_GAP_S - 1)
    assert notices.due(ready=True, idle=True, dual_ok=True) is None
    notices.dictated()
    assert notices.due(ready=True, idle=True, dual_ok=True) == "dual"
    assert prefs["dual_prompted"] is True
    assert notices.due(ready=True, idle=True, dual_ok=True) is None


def test_dual_prompt_after_ten_minutes_without_dictation(notices, clock):
    assert notices.due(ready=True, idle=True, dual_ok=True) == "ready"
    clock.advance(READY_GAP_S)
    assert notices.due(ready=True, idle=True, dual_ok=True) == "dual"


def test_dual_prompt_postponed_while_busy_and_skipped_when_already_on(notices, prefs):
    prefs["ready_noticed"] = True
    assert notices.due(ready=True, idle=False, dual_ok=True) is None
    assert notices.due(ready=True, idle=True, dual_ok=False) is None
    prefs["dual_engine"] = True
    assert notices.due(ready=True, idle=True, dual_ok=True) is None
    assert prefs["dual_prompted"] is False


def test_existing_user_gets_dual_prompt_without_waiting(notices, prefs):
    prefs["ready_noticed"] = True
    assert notices.due(ready=True, idle=True, dual_ok=True) == "dual"
