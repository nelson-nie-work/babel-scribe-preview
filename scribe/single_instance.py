"""用 Win32 命名互斥量防止重复启动。"""
import ctypes
from ctypes import wintypes

_ERROR_ALREADY_EXISTS = 183
_held = {}

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
_k32.CreateMutexW.restype = wintypes.HANDLE


def acquire(name: str = "BABEL_Scribe_SingleInstance") -> bool:
    """拿到互斥量返回 True; 已被(任何进程, 包括本进程)持有返回 False。
    句柄留在模块里, 进程退出时由系统释放。"""
    if name in _held:
        return False
    h = _k32.CreateMutexW(None, False, name)
    if not h or ctypes.get_last_error() == _ERROR_ALREADY_EXISTS:
        return False
    _held[name] = h
    return True
