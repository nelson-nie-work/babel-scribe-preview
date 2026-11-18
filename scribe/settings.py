"""本机设置与 Windows 登录启动。"""
import json
import os
import subprocess
import sys
import winreg
from pathlib import Path
from tempfile import NamedTemporaryFile

from scribe.paths import installed_launcher, settings_dir

MODES = ("gpt", "deepseek", "auto")
STYLES = ("punctuation", "natural", "formal")
HOTKEYS = {"right_ctrl": ("右 Ctrl", 0xA3), "left_ctrl": ("左 Ctrl", 0xA2),
           "left_alt": ("左 Alt", 0xA4), "f8": ("F8", 0x77)}
DEFAULTS = {
    "llm_mode": "gpt", "style": "natural", "hotkey": "right_ctrl", "microphone": "",
    "dual_engine": False, "smart_polish": False, "idle_release": False, "typing_apps": [],
    "polish_user_off": False, "services_migrated": False,
    "dual_prompted": False, "ready_noticed": False, "startup_defaulted": False,
}
_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def _startup_name() -> str:
    return "BABEL Scribe Preview" if installed_launcher() else "BABEL Scribe"


def _path():
    return settings_dir() / "settings.json"


def exists() -> bool:
    return _path().is_file()


def first_run_defaults(prefs: dict, *, installed: bool, existed: bool) -> tuple[dict, bool]:
    """安装版新用户默认开机启动；已有设置的老用户不改开机启动、不再弹就绪通知。"""
    if prefs["startup_defaulted"]:
        return prefs, False
    changes = {}
    if installed:
        changes["startup_defaulted"] = True
    if existed:
        changes["ready_noticed"] = True
    return {**prefs, **changes}, installed and not existed


def load(*, polish_available: bool = False) -> dict:
    """已有选择优先。未设置智能整理时，只有配置了服务才默认开启。"""
    defaults = {**DEFAULTS, "smart_polish": polish_available}
    d = dict(defaults)
    try:
        raw = json.loads(_path().read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            d.update(raw)
    except (OSError, ValueError):
        pass
    for field, choices in (("llm_mode", MODES), ("style", STYLES), ("hotkey", tuple(HOTKEYS))):
        if d[field] not in choices:
            d[field] = DEFAULTS[field]
    for field in ("dual_engine", "smart_polish", "idle_release", "polish_user_off", "services_migrated",
                  "dual_prompted", "ready_noticed", "startup_defaulted"):
        if not isinstance(d[field], bool):
            d[field] = defaults[field]
    if not isinstance(d["microphone"], str):
        d["microphone"] = ""
    apps = d["typing_apps"]
    d["typing_apps"] = sorted({app.casefold() for app in apps if isinstance(app, str) and app}) \
        if isinstance(apps, list) else []
    return d


def apply_service_change(prefs: dict, before: int, after: int) -> dict:
    """服务列表从空变为非空时打开智能整理, 除非用户手动关过。"""
    if before == 0 and after > 0 and not prefs["polish_user_off"] and not prefs["smart_polish"]:
        return {**prefs, "smart_polish": True}
    return prefs


def save(d: dict) -> None:
    path = _path()
    pending = None
    try:
        with NamedTemporaryFile("w", dir=path.parent, encoding="utf-8", delete=False) as stream:
            pending = Path(stream.name)
            json.dump(d, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        pending.replace(path)
    finally:
        if pending is not None:
            pending.unlink(missing_ok=True)


def _startup_command() -> str:
    launcher = installed_launcher()
    if launcher:
        return subprocess.list2cmdline([str(launcher)])
    exe = Path(sys.executable).with_name("pythonw.exe")
    repo = Path(__file__).resolve().parent.parent
    code = f"import sys;sys.path.insert(0,{str(repo)!r});from scribe.app import main;main()"
    return subprocess.list2cmdline([str(exe), "-B", "-c", code])


def startup_enabled() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            return bool(winreg.QueryValueEx(key, _startup_name())[0])
    except FileNotFoundError:
        return False


def set_startup(enabled: bool) -> None:
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, _RUN_KEY, access=winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, _startup_name(), 0, winreg.REG_SZ, _startup_command())
        else:
            try:
                winreg.DeleteValue(key, _startup_name())
            except FileNotFoundError:
                pass
