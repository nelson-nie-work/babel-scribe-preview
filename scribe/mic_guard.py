"""麦克风守护：首选断开时改用系统默认并提示一次，空闲时发现首选回来就静默切回。从不写偏好。"""
from collections.abc import Callable

UNAVAILABLE = "这支麦克风当前不可用，请重新连接或选系统默认。"
CANNOT_OPEN = "无法启用这支麦克风，请选其他设备。"


class MicGuard:
    def __init__(self, *, devices: Callable[[bool], dict[str, int | None]],
                 open_stream: Callable[[int | None], object], preferred: Callable[[], str],
                 notify: Callable[[str], None], log: Callable[[str], None] = lambda line: None):
        self._devices = devices
        self._open = open_stream
        self._preferred = preferred
        self._notify = notify
        self._log = log
        self.stream = None
        self.current = ""
        self._index: int | None = None
        self._notified = False          # 本次断开已提示过

    def _stop(self) -> None:
        stream, self.stream = self.stream, None
        if stream is not None:
            try:
                stream.stop()
            except Exception as error:
                self._log("mic stop error: %s" % type(error).__name__)

    def _try_open(self, name: str, index: int | None) -> bool:
        try:
            self.stream = self._open(index)
        except Exception as error:
            self._log("mic open error: %s" % type(error).__name__)
            self.stream = None
            return False
        self.current, self._index = name, index
        return True

    def _connect(self, refresh: bool) -> bool:
        devices = self._devices(refresh)
        pref = self._preferred()
        if pref and pref in devices and self._try_open(pref, devices[pref]):
            self._notified = False
            return True
        if self._try_open("", None):
            if pref and not self._notified:
                self._notified = True
                self._notify(f"麦克风 {pref} 已断开，改用系统默认")
            return True
        return False

    def start(self) -> bool:
        return self._connect(False)

    def before_record(self) -> bool:
        if self.stream is not None and self.stream.healthy():
            return True
        self._stop()
        return self._connect(True)

    def idle_check(self) -> None:
        pref = self._preferred()
        if not pref or self.current == pref:
            return
        self._stop()
        self._connect(True)

    def switch(self, name: str) -> None:
        devices = self._devices(False)
        if name not in devices:
            raise ValueError(UNAVAILABLE)
        old_name, old_index = self.current, self._index
        self._stop()
        if self._try_open(name, devices[name]):
            self._notified = False
            return
        if not self._try_open(old_name, old_index):
            self._log("mic restore failed")
        raise ValueError(CANNOT_OPEN)
