# 构建 Windows CPU 预览安装包

在 Windows x64 上使用 CPython 3.14.7 构建。安装 `runtime-lock.txt` 中的版本至构建用虚拟环境。构建脚本使用该环境中实际安装的 wheels，将依赖副本和其自带的许可文件放入安装包。

1. 从 [Python 3.14.7](https://www.python.org/downloads/release/python-3147/) 下载 Windows embeddable package (64-bit)，从 [NSIS 3.13](https://sourceforge.net/projects/nsis/files/NSIS%203/3.13/nsis-3.13.zip/download) 下载 `nsis-3.13.zip`。把两个 ZIP 放入同一个独立工作目录。脚本会核对下载文件的 SHA-256。
2. 构建脚本会调用 Windows 的 .NET Framework C# 编译器生成图形启动器，再运行 NSIS。每次构建使用新的工作目录和输出文件名。

   ```powershell
   py -3.14 -m venv .build-venv
   .\.build-venv\Scripts\python.exe -m pip install --only-binary :all: -r packaging\windows\runtime-lock.txt
   .\.build-venv\Scripts\python.exe packaging\windows\build_windows.py --work C:\path\to\build-inputs --output C:\path\to\BABEL-Scribe-Setup-x64-0.1.0-alpha-candidate.exe
   ```

使用本机模型下载方式，安装器包内不含模型权重和 NVIDIA 库。构建输出含 `runtime-inventory.txt`，可核对实际纳入的包与版本。
