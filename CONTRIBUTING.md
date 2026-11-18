# Contributing / 参与贡献

Thanks for helping. Bug reports from real use are as valuable as code; the [issue templates](https://github.com/nelson-nie-work/babel-scribe-preview/issues/new/choose) list what to include. 欢迎参与，真实使用的反馈和代码同样有价值。

## How this repository works / 仓库怎么运作

This public repository is published from the maintainer's development tree at each release. Issues and pull requests are reviewed here. Accepted changes are carried into the next release and credited in the [CHANGELOG](CHANGELOG.md). Please open an issue before starting a large change so we can agree on the approach. 公开仓库在每次发布时从开发主线同步。PR 在这里评审，接受的改动会进入下一个版本并在 CHANGELOG 致谢。较大的改动请先开 issue 讨论。

## Set up / 准备环境

Windows x64 and Python 3.14:

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:QT_QPA_PLATFORM = 'offscreen'
.\.venv\Scripts\python.exe -m pytest -q -m "not model and not gpu"
.\.venv\Scripts\python.exe -m scribe
```

Tests marked `model` need the cached SenseVoice model; `gpu` needs an NVIDIA GPU and the Whisper model. CI runs the rest on Windows. 标记为 `model`、`gpu` 的测试需要模型缓存或 NVIDIA 显卡，其余测试由 CI 在 Windows 上运行。

## Where things are / 代码地图

| Step | Files |
| --- | --- |
| Hotkey and recording | `scribe/hotkey.py`, `scribe/controller.py`, `scribe/audio.py` |
| Recognition | `scribe/asr/sensevoice.py`, `scribe/asr/whisper.py`, `scribe/pipeline.py` |
| Dictionary and corrections | `scribe/vocab.py`, `scribe/vocab_store.py`, `scribe/pinyin.py`, `scribe/learning.py`, `scribe/edit_watch.py` |
| Online cleanup and safety checks | `scribe/llm.py`, `scribe/fuse.py` |
| Typing at the cursor | `scribe/output.py` |
| Tray, capsule and windows | `scribe/tray.py`, `scribe/capsule.py`, `scribe/preview.py`, `scribe/*_page.py` |
| Benchmark | `bench/run_bench.py` |

## Good first contributions / 适合上手的任务

- **Expand the pinyin table.** `scribe/pinyin.py` covers about 100 characters, so homophone checks miss most words. 拼音表只覆盖约 100 字。
- **App compatibility reports.** Try dictation in the apps you use (IDEs, browsers, chat apps) with a Chinese IME on, and report where text is missing or garbled. 在常用应用里测试并报告兼容性。
- **English UI.** The interface strings are Chinese only; a translation layer would open Scribe to more users. 界面目前只有中文。

## Ground rules / 约定

- Never commit API keys, private dictation, recordings or personal paths. Use sample sentences in tests and screenshots. 不提交密钥、私人听写、录音或个人路径。
- Keep the default path on device. Anything that sends data online must be opt-in and documented. 默认在本机处理，联网功能必须由用户开启并写明。
- Add or update tests with behaviour changes. 改行为时补测试。
