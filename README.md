# BABEL Scribe

**按住右 Ctrl 说话，松手后文字出现在光标处。** BABEL Scribe 是 Windows 上的语音键盘，支持本机识别、个人词库和听写历史。

BABEL Scribe is a Windows voice keyboard with on-device recognition, a personal dictionary, and local dictation history.

> v0.1.0-alpha 源码预览 · Windows x64。可下载 CPU 安装包，也可从源码运行。

[从源码启动](#从源码启动) · [听写模式](#听写模式) · [数据与在线整理](#数据与在线整理)

## 下载 Windows x64 CPU 安装包

[下载 BABEL Scribe CPU 安装包（约 115 MB）](https://github.com/nelson-nie-work/babel-scribe-preview/releases/download/v0.1.0-alpha-cpu-preview/BABEL-Scribe-Setup-x64-0.1.0-alpha-candidate.exe) · [查看发行说明和 SHA-256](https://github.com/nelson-nie-work/babel-scribe-preview/releases/tag/v0.1.0-alpha-cpu-preview)

安装后从开始菜单打开 **BABEL Scribe Preview → BABEL Scribe**。首次启动需联网下载约 240 MB 的 SenseVoice 模型。托盘显示「就绪（单引擎）」后，按住右 Ctrl 听写。模型下载后可离线使用。

本安装版使用 SenseVoice，无需 NVIDIA 显卡。OpenAI 等在线整理服务可按需配置。Whisper 双引擎目前可通过源码版使用，NVIDIA 安装版将另行提供。此预览安装包未签名，下载后可用发行页的 SHA-256 校验。

## 核心功能

### 听写到光标处

![听写演示，先在胶囊预览，松开右 Ctrl 后把整句输入到光标处](media/dictation.gif)

<details>
<summary>选词即收</summary>

在当前输入框选中正确写法，按右 Ctrl + L 加入词库。刚加错也可以撤销。

![选词即收演示](media/quick-add.gif)

</details>

<details>
<summary>改完顺手学</summary>

听错一个词时，在原输入框里选中并替换完整错词。出现提示后，点「记住」保存正确写法。

![改完顺手学演示](media/learn-after-edit.gif)

</details>

<details>
<summary>编辑词库</summary>

在主界面的「词库」搜索词条，在右侧编辑正确写法、常见错写或备注。

![编辑词库演示](media/dictionary.gif)

</details>

<details>
<summary>找回听写历史</summary>

按文字或应用搜索。选中记录后可以复制最终文字，也可以展开对照原始识别。

![听写历史演示](media/history.gif)

</details>

演示使用示例数据，按步骤展示 Windows 界面和操作。

## 可选 NVIDIA 加速

开启双引擎后，SenseVoice 仍在 CPU 上运行，Whisper Large V3 Turbo 通过 NVIDIA GPU 增加一份识别结果。使用 CPU 听写可以跳过这一节。首次启用双引擎还会下载约 1.62 GB 的 Whisper 模型，驱动、CUDA 和 cuDNN 占用另计。

1. 安装支持 CUDA 12 的 NVIDIA 驱动、CUDA 12 的 cuBLAS 运行库和适配 CUDA 12 的 cuDNN 9。使用 [faster-whisper 的 GPU 要求](https://github.com/SYSTRAN/faster-whisper#gpu)及 [NVIDIA 的 Windows cuDNN 安装说明](https://docs.nvidia.com/deeplearning/cudnn/installation/latest/windows.html)选择版本。可以从 [NVIDIA 的 CUDA 12.9 下载页](https://developer.nvidia.com/cuda-12-9-1-download-archive)取得 Windows x64 Toolkit。
2. 找到实际装有 `cublas64_12.dll`、`cublasLt64_12.dll` 和 `cudnn64_9.dll` 的文件夹。若它们已经在系统的搜索路径中，可以跳过以下命令。否则打开 PowerShell，替换示例目录后运行。

   ```powershell
   [Environment]::SetEnvironmentVariable(
       'SCRIBE_CUDA_DLL_DIRS',
       'C:\path\to\CUDA\bin;C:\path\to\cuDNN\bin',
       'User'
   )
   ```

3. 从托盘退出 Scribe，用下方的源码启动命令重新打开。按「听写模式」开启双引擎，等待 Whisper 下载和加载。

## 听写模式

**默认使用 SenseVoice 单引擎。** 首次设置时，已配置整理服务则默认开启智能整理，优先使用 GPT。还没配置服务时直接出字。双击托盘图标，进入「通用 → 识别与整理」。

| 模式 | 如何切换 |
| --- | --- |
| 单引擎直出 | 关闭「双引擎识别」和「智能整理」。SenseVoice 在本机识别，词库仍可纠错。 |
| 单引擎 + 智能整理 | 配置下文的整理服务，关闭「双引擎识别」，开启「智能整理」。选「GPT」时优先用 OpenAI。此模式无需 NVIDIA。 |
| 双引擎 + GPT 整理 | 完成上面的 NVIDIA 准备，并配置下文的 OpenAI API 后，打开「双引擎识别」和「智能整理」，将「整理服务」选为「GPT」，「整理程度」选为「自然」，等待托盘就绪。 |

双引擎下，SenseVoice 用 CPU、Whisper Large V3 Turbo 用 NVIDIA GPU，两者并行识别。两份识别结果一致且无需整理时直接出字。需要整理时，程序会把转写文字和相关词条交给 GPT，录音留在本机。关闭两个开关即可回到单引擎直出。

### 26 句内部基准

| 模式 | 平均错字率 | 松手到文字生成的中位耗时 |
| --- | ---: | --- |
| SenseVoice 单引擎直出 | **17.45%** | **83 毫秒** |
| SenseVoice 单引擎 + GPT-5.4 mini | **11.66%** | **1.04 秒**，九成在 **1.78 秒**内 |
| SenseVoice + Whisper 双引擎 + GPT-5.4 mini | **6.55%、8.10%**（历史两次复跑） | 直接出字约 **0.20 秒**，调用 GPT 整理约 **1.11–1.16 秒** |

三档使用同一批内部录音，样本只用于项目验收。表中是这组材料的测量结果。单引擎加 GPT 的 26 句中，25 句采用 GPT 整理结果，1 句回退到本地结果，均纳入统计。双引擎的耗时按直出和整理两条路径分别统计。错字率先统一大小写、全半角并去掉标点和空白，再按句计算并取平均。计时到生成最终文字为止，粘贴或逐字上屏另计。

## 从源码启动

1. 安装 [Windows x64 Python 3.14](https://www.python.org/downloads/windows/)。将源码解压到当前用户有写入权限的目录。
2. 在源码目录打开 PowerShell，运行以下命令。

   ```powershell
   py -3.14 -m venv .venv
   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
   .\.venv\Scripts\python.exe -m scribe
   ```

3. 首次启动需联网下载约 240 MB 的 SenseVoice CPU 模型。托盘显示「就绪」后，可在记事本试一句听写。用 `.\.venv\Scripts\pythonw.exe -m scribe` 可以后台启动。模型已下载时，可在启动前设置 `$env:HF_HUB_OFFLINE = '1'`，只从本机缓存加载。

## 快捷操作

| 操作 | 用法 |
| --- | --- |
| 听写 | 按住右 Ctrl，松手出字 |
| 免手持 | 双击右 Ctrl 开始，再按一次结束 |
| 取消录音 | 按 Esc |
| 收词 | 选中正确词，按右 Ctrl + L |
| 打开主界面 | 双击托盘图标 |
| 为某应用改用逐字输入 | 历史详情「复制」旁的「…」启用，也从同一菜单恢复默认粘贴 |

默认用粘贴输出。如果剪贴板被占用，或其内容无法安全备份，Scribe 会在原输入窗口仍有焦点时改用逐字输入，保留你的剪贴板。这只对当前这句生效。若输入目标已经改变，仍可从历史找回。

## 数据与在线整理

- SenseVoice 和 Whisper 在设备上处理录音。听写历史在本机保留 30 天。
- 安装版的设置、日志和历史位于 `%LOCALAPPDATA%\BABEL Scribe\User Data`，其下 `data` 文件夹保存 `vocab.json` 和可选的 `keys.json`。默认模型缓存位于 `%LOCALAPPDATA%\BABEL Scribe\Model Cache`。
- 源码版的设置、日志和历史位于 `%APPDATA%\BABEL Scribe`。词库和可选服务配置位于源码目录同级的 `babel-data`。词库首次运行时创建。
- 使用 GPT 时，在 `keys.json` 的 `openai.api_key` 中配置自己的 OpenAI API 密钥，保存后重启 Scribe。也可在 `deepseek` 或 `glm` 项配置相应服务的 `api_key`。已保存的设置会继续生效。如果此前关闭了「智能整理」，配置服务后可手动开启。在线整理会发送转写文字和本句相关词条，录音留在本机。请求失败时保留本机识别结果。

## 让个人词库纠正常见错写

「改完顺手学」确认后收录正确写法。如果同一个词反复听错，可在词库的「常被听成」补上实际错写，例如把「中心国际」对应到「中芯国际」。这条规则在 SenseVoice 单引擎直出时也会生效。人名、项目名等专有词可按词开启同音自动纠正。

## 已知问题

- 在线整理在中英混说时可能把英文词翻成中文。需要逐字保留英文时，可以关闭「智能整理」，或试用「只加标点」。
- 默认中文输入法正在选字时，改完顺手学可能提前识别到拼音。可以跳过提示，选字完成后通过词库手动收录。

## 反馈和开发

安装、听写和应用兼容问题可通过 GitHub Issues 反馈。请提供版本、Windows 版本、是否有 NVIDIA 显卡、复现步骤和预期结果。请用示例句替代个人听写，发日志前移除密钥、账号和个人路径。

在 Windows 上运行与模型无关的测试。示例音频未随源码提供，依赖真实音频的模型测试可使用下方说明准备材料。

```powershell
$env:QT_QPA_PLATFORM = 'offscreen'
.\.venv\Scripts\python.exe -m pytest -q -m "not model and not gpu"
```

模型和 GPU 测试需要相应缓存及硬件。若要运行示例转写测试，可自行准备一句「帮我把这个文件发给张总」的获准使用音频，保存为 `tests/fixtures/s1_short.mp3`。安装包构建见 [Windows 打包说明](packaging/windows/README.md)。

## 许可

BABEL Scribe 自有源码采用 [MIT 许可证](LICENSE)，版权署名为 Houseworks。模型、Python 依赖和运行库各自适用上游条款，见[第三方许可清单](THIRD_PARTY_NOTICES.md)。
