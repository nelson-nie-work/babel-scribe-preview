"""设置、日志与共享数据目录。"""
import os
import sys
from pathlib import Path

from scribe.config import APP_NAME

_REPO = Path(__file__).resolve().parent.parent


def installed_launcher() -> Path | None:
    launcher = _REPO / f"{APP_NAME}.exe"
    runtime = Path(sys.executable).resolve().parent
    return launcher if runtime == _REPO / "runtime" and launcher.is_file() else None


def settings_dir() -> Path:
    """安装版数据与源码版配置分开保存。"""
    if installed_launcher():
        d = Path(os.environ["LOCALAPPDATA"]) / APP_NAME / "User Data"
    else:
        d = Path(os.environ["APPDATA"]) / APP_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def logs_dir() -> Path:
    d = settings_dir() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def shared_data_dir() -> Path:
    """本机在线服务配置与个人词库目录：仓库同级的 babel-data。"""
    return settings_dir() / "data" if installed_launcher() else _REPO.parent / "babel-data"
