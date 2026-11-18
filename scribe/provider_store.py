"""整理服务列表的本机加密存储(Windows DPAPI, 当前用户范围)、旧 keys.json 导入与老用户顺序迁移。

永不打印或记录 api_key。
"""
import base64
import ctypes
import hashlib
import json
import os
import re
import time
from ctypes import wintypes
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Callable

from scribe import keys

_TEXT_FIELDS = ("name", "base_url", "model", "api_key")
_MIGRATION = {
    "gpt": ("openai", "deepseek", "glm"),
    "deepseek": ("deepseek", "openai", "glm"),
    "auto": ("openai", "deepseek", "glm"),
}


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _crypt(data: bytes, encrypt: bool) -> bytes:
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32")
    buffer = ctypes.create_string_buffer(data, len(data))
    source = _Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    result = _Blob()
    call = crypt32.CryptProtectData if encrypt else crypt32.CryptUnprotectData
    call.argtypes = [ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                     ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob)]
    call.restype = wintypes.BOOL
    if not call(ctypes.byref(source), None, None, None, None, 0, ctypes.byref(result)):
        raise OSError("DPAPI %s failed (%d)" % ("protect" if encrypt else "unprotect", ctypes.get_last_error()))
    try:
        return ctypes.string_at(result.pbData, result.cbData)
    finally:
        kernel32.LocalFree(result.pbData)


def protect(data: bytes) -> bytes:
    return _crypt(data, True)


def unprotect(blob: bytes) -> bytes:
    return _crypt(blob, False)


def store_path(data_dir: Path) -> Path:
    """每台电脑各用一个文件: DPAPI 密文只有本机本用户能解开, 同步盘里共用一个文件会互相覆盖。"""
    machine = re.sub(r"[^A-Za-z0-9_-]+", "-", os.environ.get("COMPUTERNAME", "") or "this-pc").strip("-")
    return Path(data_dir) / ("providers.%s.json" % (machine or "this-pc"))


def _fingerprint(preset, api_key) -> str:
    return hashlib.sha256(("%s\0%s" % (preset, str(api_key or "").strip())).encode("utf-8")).hexdigest()[:16]


class ProviderStore:
    def __init__(self, path: Path, *, protect: Callable[[bytes], bytes] = protect,
                 unprotect: Callable[[bytes], bytes] = unprotect):
        self.path = Path(path)
        self._protect, self._unprotect = protect, unprotect
        self.unreadable = False
        self.set_aside: Path | None = None      # 解不开的旧文件被改名保留到这里
        self.imported: set = set()              # 已从 keys.json 导入过的(服务商, 密钥)指纹

    def load(self) -> list:
        """缺文件 → [];解密或解析失败 → [] 且 unreadable 为 True。"""
        self.unreadable = False
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return []
        except OSError:
            self.unreadable = True
            return []
        try:
            blob = base64.b64decode(json.loads(raw)["blob"], validate=True)
            data = json.loads(self._unprotect(blob).decode("utf-8"))
            if isinstance(data, list):                      # 早期格式: 只有服务列表
                data = {"entries": data, "imported": []}
            entries, imported = data["entries"], data.get("imported", [])
            if not isinstance(entries, list) or not isinstance(imported, list):
                raise ValueError("bad payload")
            self.imported = {str(f) for f in imported}
            return [dict(e) for e in entries if isinstance(e, dict)]
        except (OSError, ValueError, KeyError, TypeError):
            self.unreadable = True
            return []

    def save(self, entries: list) -> None:
        clean = [{k: (str(v).strip() if k in _TEXT_FIELDS and v is not None else v) for k, v in e.items()}
                 for e in entries]
        payload_data = {"entries": clean, "imported": sorted(self.imported)}
        blob = self._protect(json.dumps(payload_data, ensure_ascii=False).encode("utf-8"))
        payload = {"version": 1, "blob": base64.b64encode(blob).decode("ascii")}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.unreadable and self.set_aside is None and self.path.exists():
            # 解不开的文件可能是别的账户或别的电脑的, 不覆盖, 改名保留
            aside = self.path.with_name("%s.unreadable-%s.json" % (self.path.name.split(".")[0],
                                                                    time.strftime("%Y%m%d-%H%M%S")))
            self.path.replace(aside)
            self.set_aside = aside
        pending = None
        try:
            with NamedTemporaryFile("w", dir=self.path.parent, encoding="utf-8", prefix=".providers-",
                                    suffix=".tmp", delete=False) as stream:
                pending = Path(stream.name)
                json.dump(payload, stream)
                stream.flush()
                os.fsync(stream.fileno())
            pending.replace(self.path)
            pending = None
        finally:
            if pending is not None:
                pending.unlink(missing_ok=True)

    def import_legacy(self, entries: list, keys_path: Path) -> list:
        """把 keys.json 里从没导入过、也不在列表里的(服务商, 密钥)组合追加到末尾。
        导入过的组合记下来: 用户在界面里删掉或换了密钥, 不会在下次启动时又冒出来。"""
        have = {_fingerprint(e.get("preset"), e.get("api_key")) for e in entries}
        added = []
        for e in keys.legacy_entries(keys_path):
            mark = _fingerprint(e["preset"], e["api_key"])
            if mark not in have and mark not in self.imported:
                added.append(e)
            self.imported.add(mark)
        return list(entries) + added


def migrate_order(entries: list, llm_mode: str) -> list:
    """按老用户原来的整理服务模式排三家预设, 其余保持相对顺序排在后面。"""
    wanted = _MIGRATION.get(llm_mode, _MIGRATION["gpt"])
    first = [e for name in wanted for e in entries if e.get("preset") == name]
    return first + [e for e in entries if e.get("preset") not in wanted]
