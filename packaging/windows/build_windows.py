"""Build the per-user Windows preview with an isolated CPython runtime."""
from argparse import ArgumentParser
from collections import deque
from importlib import metadata
from pathlib import Path, PurePosixPath
import hashlib
import os
import shutil
import subprocess
import sys
import zipfile

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOTS = ("PySide6-Essentials", "faster-whisper", "sounddevice", "numpy", "pynput",
         "sherpa-onnx", "openai", "huggingface-hub")
TOOL_HASHES = {
    "python-3.14.7-embed-amd64.zip": "d297e5ff019966817ad8502465176139f2d3d840fa4ed84b13bed399a6ab1f15",
    "nsis-3.13.zip": "ba63dffc4410ee89193e1cb5a41989991bd77c61068da17e3156d136b7b0b3d8",
}
SOURCE = Path(__file__).resolve().parents[2]


def verified_extract(archive: Path, target: Path) -> None:
    with archive.open("rb") as file:
        if hashlib.file_digest(file, "sha256").hexdigest() != TOOL_HASHES[archive.name]:
            raise ValueError(f"Upstream SHA-256 mismatch: {archive.name}")
    with zipfile.ZipFile(archive) as zip_file:
        for name in zip_file.namelist():
            part = PurePosixPath(name)
            if part.is_absolute() or ".." in part.parts or any(":" in element for element in part.parts):
                raise ValueError(f"Unsafe archive path: {name}")
        zip_file.extractall(target)


def distributions():
    pending = deque(ROOTS)
    selected = {}
    while pending:
        name = pending.popleft()
        key = canonicalize_name(name)
        if key in selected:
            continue
        distribution = selected[key] = metadata.distribution(name)
        for entry in distribution.requires or []:
            requirement = Requirement(entry)
            if requirement.marker and not requirement.marker.evaluate({"extra": ""}):
                continue
            installed = metadata.version(requirement.name)
            if installed not in requirement.specifier:
                raise ValueError(f"{requirement.name} {installed} does not satisfy {requirement.specifier}")
            pending.append(requirement.name)
    return selected


def copy_runtime_packages(destination: Path) -> list[str]:
    selected = distributions()
    for name in selected:
        if name.startswith("nvidia-") or name in {"pyside6", "pyside6-addons", "pyside6-webengine"}:
            raise ValueError(f"Unexpected CPU installer dependency: {name}")
    for package in selected.values():
        root = Path(package.locate_file("")).resolve()
        for entry in package.files or []:
            part = PurePosixPath(entry)
            if part.is_absolute() or ".." in part.parts or "__pycache__" in part.parts or part.suffix == ".pyc":
                continue
            source = Path(package.locate_file(entry)).resolve(strict=True)
            if not source.is_relative_to(root) or not source.is_file():
                raise ValueError(f"Unowned wheel path: {entry}")
            target = destination.joinpath(*part.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
    return [f"{name}=={package.version}" for name, package in sorted(selected.items())]


def main() -> None:
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--work", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if sys.platform != "win32" or sys.version_info[:3] != (3, 14, 7) or sys.maxsize < 2**32:
        raise SystemExit("Build with Windows x64 CPython 3.14.7")
    work, output = args.work.resolve(), args.output.resolve()
    stage, tools = work / "stage", work / "nsis"
    if output.exists():
        raise FileExistsError(output)
    stage.mkdir()
    tools.mkdir()
    runtime = stage / "runtime"
    runtime.mkdir()
    verified_extract(work / "python-3.14.7-embed-amd64.zip", runtime)
    verified_extract(work / "nsis-3.13.zip", tools)
    (runtime / "python314._pth").write_text("python314.zip\n.\nLib\\site-packages\n..\n",
                                             encoding="ascii", newline="\n")
    shutil.copytree(SOURCE / "scribe", stage / "scribe", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for file in ("README.md", "LICENSE", "THIRD_PARTY_NOTICES.md"):
        shutil.copyfile(SOURCE / file, stage / file)
    for directory, names in (
        ("media", ("dictation.gif", "quick-add.gif", "learn-after-edit.gif", "dictionary.gif", "history.gif")),
        ("packaging/windows", ("README.md", "runtime-lock.txt")),
    ):
        (stage / directory).mkdir(parents=True)
        for name in names:
            shutil.copyfile(SOURCE / directory / name, stage / directory / name)
    package_list = copy_runtime_packages(runtime / "Lib" / "site-packages")
    lock = (SOURCE / "packaging" / "windows" / "runtime-lock.txt").read_text(encoding="utf-8").splitlines()
    if package_list != lock:
        raise ValueError("Build environment does not match runtime-lock.txt")
    (stage / "runtime-inventory.txt").write_text("\n".join(package_list) + "\n", encoding="utf-8")
    (stage / "scribe-installed.marker").write_text("BABEL Scribe preview install layout\n", encoding="utf-8")
    nsis = next(tools.rglob("makensis.exe"))
    copying = next(tools.rglob("COPYING"))
    shutil.copyfile(copying, stage / "nsis-COPYING.txt")

    csc = Path(os.environ["SystemRoot"]) / "Microsoft.NET" / "Framework64" / "v4.0.30319" / "csc.exe"
    icon = SOURCE / "scribe" / "assets" / "scribe.ico"
    subprocess.run([str(csc), "/nologo", "/target:winexe", "/platform:x64", "/codepage:65001",
                    "/reference:System.Windows.Forms.dll", f"/win32icon:{icon}",
                    f"/out:{stage / 'BABEL Scribe.exe'}", str(Path(__file__).with_name("Launcher.cs"))],
                   check=True, creationflags=subprocess.CREATE_NO_WINDOW)
    check = ("import sys; import numpy, sounddevice, sherpa_onnx, ctranslate2, faster_whisper, openai;"
             "from PySide6.QtWidgets import QApplication; "
             "from scribe.paths import installed_launcher; from scribe.settings import DEFAULTS; "
             "assert sys.flags.isolated and installed_launcher() and not DEFAULTS['dual_engine']; "
             "print('Embedded runtime imports OK')")
    subprocess.run([str(runtime / "python.exe"), "-B", "-c", check], cwd=stage,
                   check=True, creationflags=subprocess.CREATE_NO_WINDOW)
    subprocess.run([str(nsis), "/V2", f"/DSTAGE={stage}", f"/DOUTPUT={output}",
                    f"/DICON={icon}", str(Path(__file__).with_name("setup.nsi"))],
                   check=True, creationflags=subprocess.CREATE_NO_WINDOW)
    print("installer", output.stat().st_size)
    print("bundled_packages", len(package_list))
    print("payload", sum(file.stat().st_size for file in stage.rglob("*") if file.is_file()))


if __name__ == "__main__":
    main()
