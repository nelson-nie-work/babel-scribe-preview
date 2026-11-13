import os

from scribe import single_instance


def test_second_acquire_fails():
    name = "BABEL_Scribe_Test_%d" % os.getpid()
    assert single_instance.acquire(name) is True
    assert single_instance.acquire(name) is False
