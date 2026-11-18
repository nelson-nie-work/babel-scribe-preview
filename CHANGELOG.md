# Changelog / 更新记录

## 0.1.3-alpha · 2026-11-18

- **Set up online cleanup in the app.** Add OpenAI, DeepSeek, GLM, OpenRouter, SiliconFlow, a local Ollama or any OpenAI-compatible service under **通用 → 识别与整理 → 整理服务**, test the connection and save. Services are tried in list order, the tray menu can switch the first one, and keys are encrypted with Windows on this PC. An existing `keys.json` is imported on first launch. 整理服务在界面里添加、测试和排序，密钥加密保存，旧的 `keys.json` 自动导入。
- **Dual engine in the installer.** With an NVIDIA GPU (4 GB VRAM or more), turn on **双引擎识别** in settings. Scribe checks the GPU and driver, then downloads NVIDIA's cuBLAS runtime and the Whisper model (about 2.2 GB) with progress, cancel and resume. Dictation keeps working on one engine while it downloads. 安装版在设置里即可开启双引擎，下载期间照常单引擎听写。
- **Model downloads show progress and recover.** Hover over the tray icon to see progress and the source. Downloads switch to a mirror when Hugging Face can't be reached, resume after interruption and are checked against SHA-256. An existing Hugging Face cache is reused without downloading again. 模型下载显示进度和来源，自动换源、断点续传、核对校验值。
- **First run.** A notice appears once when Scribe is ready. New installs start with Windows (turn it off under **通用 → 后台运行**); upgrades keep the previous setting. 首次就绪通知一次；新安装默认开机启动，升级保持原设置。
- **Microphone fallback.** If the chosen microphone is unplugged, Scribe warns once and records from the system default, then switches back when it is reconnected. 指定的麦克风断开时改用系统默认，插回后自动切回。
- **Long dictation stays complete.** Audio is split at natural pauses into pieces of at most 12 s and recognised while you are still speaking, so 30–90 s passages no longer lose large parts and text appears about 0.1 s after release. 长段听写按停顿分段、边说边识别，不再大段漏字。
- **Paste works after copying from Chrome-based apps.** Clipboard content copied from Chrome, ChatGPT, Claude and similar apps is now backed up and restored, so Scribe pastes instead of typing character by character. 从 Chrome 类应用复制后也能粘贴并还原剪贴板。
- **Natural cleanup keeps more good edits.** Case fixes, dictionary terms, stutter and filler removal and common homophone fixes are accepted; edits that add, drop or change content still fall back to the local text. 「自然」整理放行大小写、词库专名、结巴和常见同音字修正。
- **Running from source:** `requirements.txt` now includes `httpx` (needed for online cleanup) and pins `av` to a version that decodes audio correctly. 源码版依赖补上 `httpx` 并固定 `av` 版本。
- **Dictionary misspellings ignore case.** Registering `Cloud` also fixes `cloud` and `CLOUD`. 词库错写不区分大小写。

## 0.1.2-alpha · 2026-11-17

- Learn from a correction: replace a misheard word, click **Remember**, and the same mistake is fixed next time. Undo removes only the rule. 改完顺手学。
- Natural cleanup preserves English in mixed dictation, key actions, conditions and complete sentences, and falls back to the local text when an edit fails that check. 「自然」整理保留更多原话。
- In-place upgrade from 0.1.1-alpha keeps the dictionary, saved corrections and settings. 0.1.1-alpha was an internal build.

## 0.1.0-alpha · 2026-11-13

- First public Windows CPU preview: hold-to-talk dictation with SenseVoice on device, personal dictionary with quick add, dictation history, optional online cleanup with your own API key. 首个公开 Windows CPU 预览版。
