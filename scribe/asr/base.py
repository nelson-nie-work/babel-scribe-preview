"""识别引擎接口: 以后加 SenseVoice 等引擎都实现它。"""
from typing import Protocol

import numpy as np


class AsrEngine(Protocol):
    ready: bool
    failed: bool

    def load(self) -> None: ...

    def transcribe(self, audio: np.ndarray) -> str: ...
