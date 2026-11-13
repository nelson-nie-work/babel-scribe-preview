# BABEL Scribe 第三方许可清单

源码预览包含代码、测试、图标和示例 GIF。Windows CPU 安装包另行准备。当前私有安装候选内含 CPython 3.14.7 嵌入式运行时及 43 个 Python 发行包，实际版本见 [Windows 运行环境清单](packaging/windows/runtime-lock.txt)，安装目录也提供 runtime-inventory.txt。模型权重、CUDA、cuDNN 和显卡驱动由用户从各自上游取得。

CPython 嵌入包自带许可在 runtime/LICENSE.txt，Python wheels 自带的许可位于各自的 dist-info 目录，NSIS 许可在 nsis-COPYING.txt。Qt 和 Shiboken 以独立文件动态加载。公开分发安装包前，还需确认 Qt 的 LGPL 义务，以及 PyAV wheel 实际内含 FFmpeg、libx264 和 libx265 等组件的许可、声明和源码提供方式。表格中的 PyPI 许可仅标明 Python 包本身。

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
| PyAV、FFmpeg 与 VAD | faster-whisper 的音频解码与语音活动检测 | [PyAV 上游许可](https://github.com/PyAV-Org/PyAV/blob/main/LICENSE.txt) · [FFmpeg 许可](https://ffmpeg.org/legal.html) · [Silero VAD](https://github.com/snakers4/silero-vad)。发布二进制前按所带 wheel 和资源核对实际声明。 |

发布 Windows 二进制时，优先收录实际用到的 Qt 模块，并保留选用的第三方许可及其要求的声明。Python wheel 自身标注的许可证不一定覆盖它内含的全部组件。下表是识别组件的起点。

## 本次环境中的依赖版本与元数据

| 包 | 版本 | 安装包元数据中的许可证 |
| --- | --- | --- |
| annotated-types | 0.8.0 | MIT |
| anyio | 4.15.1 | MIT |
| av | 18.1.0 | BSD-3-Clause |
| certifi | 2026.7.22 | Mozilla Public License 2.0 (MPL 2.0) |
| cffi | 2.1.1 | MIT-0 |
| click | 8.5.0 | BSD-3-Clause |
| colorama | 0.4.6 | BSD License |
| ctranslate2 | 4.8.2 | MIT |
| faster-whisper | 1.2.1 | MIT License |
| filelock | 3.32.6 | MIT |
| flatbuffers | 25.12.19 | Apache Software License |
| fsspec | 2026.7.0 | BSD-3-Clause |
| h11 | 0.16.0 | MIT License |
| hf-xet | 1.6.0 | Apache-2.0 |
| httpcore | 1.0.9 | BSD-3-Clause |
| httpcore2 | 2.12.0 | BSD-3-Clause |
| httpx | 0.28.1 | BSD License |
| httpx2 | 2.12.0 | BSD-3-Clause |
| huggingface-hub | 1.31.0 | Apache Software License |
| idna | 3.19 | BSD-3-Clause |
| iniconfig | 2.3.1 | MIT |
| jiter | 0.17.0 | MIT |
| numpy | 2.5.3 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 |
| nvidia-cublas-cu12 | 12.9.2.10 | LicenseRef-NVIDIA-Proprietary |
| nvidia-cuda-nvrtc-cu12 | 12.9.86 | Other/Proprietary License |
| nvidia-cudnn-cu12 | 9.26.0.51 | LicenseRef-NVIDIA-Proprietary |
| onnxruntime | 1.30.0 | MIT License |
| openai | 3.13.0 | Apache-2.0 |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause |
| pluggy | 1.7.0 | MIT License |
| protobuf | 7.36.1 | 3-Clause BSD License |
| pycparser | 3.0 | BSD-3-Clause |
| pydantic | 2.13.5 | MIT |
| pydantic-core | 2.46.5 | MIT |
| pygments | 2.21.0 | BSD-2-Clause |
| pynput | 1.8.2 | GNU Lesser General Public License v3 (LGPLv3) |
| pyside6 | 6.12.0 | LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only |
| pyside6-addons | 6.12.0 | LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only |
| pyside6-essentials | 6.12.0 | LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only |
| pyside6-pdf | 6.12.0.140 | LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only |
| pyside6-webengine | 6.12.0.140 | LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only |
| pytest | 9.2.0 | MIT |
| pyyaml | 6.0.3 | MIT License |
| sherpa-onnx | 2.0.1 | Apache licensed, as found in the LICENSE file |
| sherpa-onnx-core | 2.0.1 | Apache-2.0 |
| shiboken6 | 6.12.0 | LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only |
| six | 1.17.0 | MIT License |
| sniffio | 1.3.1 | MIT License ／ Apache Software License |
| sounddevice | 0.5.6 | MIT |
| tokenizers | 0.23.2 | Apache Software License |
| tqdm | 4.70.1 | MPL-2.0 AND MIT |
| truststore | 0.10.4 | MIT |
| typing-extensions | 4.16.0 | PSF-2.0 |
| typing-inspection | 0.4.4 | MIT |
