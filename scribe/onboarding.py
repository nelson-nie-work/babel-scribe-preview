"""首次体验的通知时机：就绪通知一次，双引擎提示一次，都只在空闲时弹。"""
import time
from collections.abc import Callable

READY_GAP_S = 600       # 就绪通知之后，没有听写也至少隔这么久再提示双引擎


class Notices:
    def __init__(self, prefs: Callable[[], dict], save: Callable[[str, bool], None],
                 clock: Callable[[], float] = time.monotonic):
        self._prefs = prefs
        self._save = save
        self._clock = clock
        self._ready_at: float | None = None     # 本次运行弹出就绪通知的时间
        self._dictated_since_ready = False

    def dictated(self) -> None:
        self._dictated_since_ready = True

    def due(self, *, ready: bool, idle: bool, dual_ok: bool) -> str | None:
        if not idle:
            return None
        prefs = self._prefs()
        if ready and not prefs["ready_noticed"]:
            self._save("ready_noticed", True)
            self._ready_at = self._clock()
            self._dictated_since_ready = False
            return "ready"
        if dual_ok and not prefs["dual_prompted"] and not prefs["dual_engine"]:
            waited = (self._ready_at is None or self._dictated_since_ready
                      or self._clock() - self._ready_at >= READY_GAP_S)
            if waited:
                self._save("dual_prompted", True)
                return "dual"
        return None
