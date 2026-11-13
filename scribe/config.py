"""M1 的固定参数(取值见 docs/specs 与 docs/plans 的 Global Constraints)。"""

APP_NAME = "BABEL Scribe"
APP_USER_MODEL_ID = "BABEL.Scribe"

# 音频
SAMPLE_RATE = 16000
BLOCK = 480                # 30 ms
PREROLL_MS = 300           # 按下前预录, 防吞首字
MAX_RECORD_S = 300         # 单次录音上限

# 快捷键
MIN_HOLD_S = 0.3           # 按住不足视为误触
DOUBLE_TAP_S = 0.35        # 双击窗口

# 输出
RESTORE_DELAY_S = 0.3      # 粘贴后还原剪贴板的延迟

# 胶囊
DONE_HIDE_MS = 600
ERROR_HIDE_MS = 2000

# 识别
WHISPER_MODEL = "large-v3-turbo"
WHISPER_COMPUTE = "int8_float16"
