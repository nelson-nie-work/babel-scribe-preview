# Changelog / 更新记录

## Unreleased / 未发布

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
