# BABEL Scribe 第三方许可

BABEL Scribe 自有源码采用 MIT，版权署名 Houseworks。安装包中的第三方组件分别适用自身许可。

## Windows CPU 安装包

- CPython 3.14.7 来自 Python 官方 Windows x64 嵌入包。其许可与第三方声明在 runtime/LICENSE.txt。NSIS 3.13 的许可在 nsis-COPYING.txt。
- Qt for Python 6.12.0 使用开源发行的 PySide6 Essentials 和 Shiboken6。安装包选用 Qt Core、Gui、Widgets 及 Windows、离屏、ICO 和 Windows 样式插件。动态库及 Python 扩展以独立文件安装。Qt、PySide6 许可文本、上游源码归档与说明在 third-party。
- LGPL 组件可检查、修改和替换。退出 Scribe 后，可用 ABI 兼容的自编译 Qt 动态库或 Python 绑定替换 runtime/Lib/site-packages 下相应文件。安装包不对这些文件做运行时签名校验。本许可说明不限制为调试 LGPL 库修改而进行的逆向工程。
- sherpa-onnx / sherpa-onnx-core 2.0.1 采用 Apache-2.0。内嵌的 ONNX Runtime 1.28.2 采用 MIT，许可与第三方声明见 third-party。麦克风使用 sounddevice 0.5.6 携带的 Windows x64 普通 PortAudio，相关许可见 third-party。
- pynput 1.8.2 采用 LGPLv3。上游源码归档、GPL 与 LGPL 文本随包提供。证书包 certifi 的 MPL-2.0 源码归档也随包提供。
- 其他 Python 发行包和版本见 runtime-inventory.txt。各包附带的许可文件位于 runtime/Lib/site-packages 的 dist-info。
- 本安装包提供 SenseVoice CPU 和可选在线整理。Whisper、PyAV、FFmpeg、x264、x265、NVIDIA 运行库和模型权重由此包之外的来源提供。双引擎仍可通过源码版使用。


- tqdm 4.70.1 uses MPL-2.0 and MIT. Its exact source distribution, tqdm-4.70.1.tar.gz, is included in third-party. Microsoft Visual C++ runtime files are third-party redistributable components, separate from the project MIT license. See https://learn.microsoft.com/cpp/windows/redistributing-visual-cpp-files.

## 上游源码

安装目录 third-party 包含 QtBase 6.12.0 和 PySide/Shiboken 6.12.0 的上游发布归档，以及 pynput 1.8.2 和 certifi 2026.7.22 源码归档。

- QtBase [归档](https://download.qt.io/archive/qt/6.12/6.12.0/submodules/qtbase-everywhere-src-6.12.0.tar.xz)
- PySide/Shiboken [归档](https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-6.12.0-src/pyside-setup-everywhere-src-6.12.0.tar.xz)
- pynput [版本源码](https://github.com/moses-palmer/pynput/tree/v1.8.2)
- certifi [版本源码](https://github.com/certifi/python-certifi/tree/2026.07.22)
- sherpa-onnx [版本源码](https://github.com/k2-fsa/sherpa-onnx/tree/v2.0.1)
- ONNX Runtime [版本源码](https://github.com/microsoft/onnxruntime/tree/v1.28.2)
- PortAudio [许可](https://www.portaudio.com/license.html)

## 模型

| 模型 | 代码使用的来源及文件 | 许可与处理 |
| --- | --- | --- |
| SenseVoice Small INT8 | [sherpa-onnx ONNX 转换](https://huggingface.co/csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17)，下载 `model.int8.onnx` 和 `tokens.txt`，本机合计 239,549,735 字节 | [SenseVoiceSmall 上游](https://huggingface.co/FunAudioLLM/SenseVoiceSmall)列单独模型许可。[维护者说明](https://github.com/QwenAudio/SenseVoice)指向 [FunASR Model Open Source License v1.1](https://github.com/modelscope/FunASR/blob/58830eca4012644aac0c3218c3ccc7d98f003fda/MODEL_LICENSE)，归属和模型名称条款适用。做离线分发前再核当前 ONNX 转换版本及其许可文件。 |
| Whisper Large V3 Turbo | [faster-whisper 的模型别名](https://github.com/SYSTRAN/faster-whisper/blob/master/faster_whisper/utils.py)指向 Hugging Face 的 `mobiuslabsgmbh/faster-whisper-large-v3-turbo`，目前重定向到 [CTranslate2 转换模型](https://huggingface.co/dropbox-dash/faster-whisper-large-v3-turbo)，本机文件合计 1,621,665,983 字节 | [OpenAI Whisper 代码和权重](https://github.com/openai/whisper)采用 MIT，所用转换模型页也标注 MIT。按版本留存上游出处和声明。 |

## 直接依赖与可选组件

| 组件 | 用途 | 上游许可入口 |
| --- | --- | --- |
| Python | 源码运行环境和安装版嵌入式运行时 | [PSF 许可和历史许可](https://docs.python.org/3/license.html) |
| PySide6 / Shiboken6 | Windows 界面、托盘和胶囊 | [Qt for Python 授权选项](https://doc.qt.io/qtforpython-6/commercial/index.html) · [上游代码附带的许可](https://doc.qt.io/qtforpython-6/licenses.html) |
| faster-whisper | Whisper 的 CTranslate2 推理入口，含音频解码依赖 | [MIT](https://github.com/SYSTRAN/faster-whisper/blob/master/LICENSE) |
| sounddevice | 麦克风采集，Windows wheel 内含 PortAudio | [sounddevice](https://pypi.org/project/sounddevice/) · [PortAudio 许可](https://www.portaudio.com/license.html) |
| NumPy | 音频数组和计算 | [NumPy 许可及附带组件](https://github.com/numpy/numpy/blob/main/LICENSE.txt) |
| pynput | Windows 快捷键监听 | [LGPLv3 元数据和上游入口](https://pypi.org/project/pynput/) |
| sherpa-onnx / sherpa-onnx-core | SenseVoice 推理库 | [Apache-2.0](https://github.com/k2-fsa/sherpa-onnx/blob/master/LICENSE) |
| OpenAI Python SDK | 可选在线整理服务的客户端 | [Apache-2.0](https://github.com/openai/openai-python/blob/main/LICENSE) |
| Hugging Face Hub | 模型下载和缓存 | [Apache-2.0](https://github.com/huggingface/huggingface_hub/blob/main/LICENSE) |
| pytest | 开发与测试 | [MIT](https://github.com/pytest-dev/pytest/blob/main/LICENSE) |
| NVIDIA CUDA / cuBLAS / NVRTC / cuDNN | 可选的 NVIDIA 双引擎支持 | [CUDA 12.9 EULA 及可再分发文件范围](https://docs.nvidia.com/cuda/archive/12.9.1/pdf/EULA.pdf) · [cuDNN EULA](https://docs.nvidia.com/deeplearning/cudnn/latest/reference/eula.html)。首发由用户取得兼容运行库，后续按确切二进制核对分发条款。 |
| PyAV、FFmpeg 与 VAD | faster-whisper 的音频解码与语音活动检测 | [PyAV 上游许可](https://github.com/PyAV-Org/PyAV/blob/main/LICENSE.txt) · [FFmpeg 许可](https://ffmpeg.org/legal.html) · [Silero VAD](https://github.com/snakers4/silero-vad)。源码版可选依赖，CPU 安装包未分发这些文件。 |

发布 Windows 二进制时，优先收录实际用到的 Qt 模块，并保留选用的第三方许可及其要求的声明。Python wheel 自身标注的许可证不一定覆盖它内含的全部组件。下表是识别组件的起点。
