"""只存本机、滚动保留 30 天的听写文字。"""
import sqlite3
import time
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from scribe.pipeline import Result

RETENTION_S = 30 * 24 * 60 * 60
_APP_NAMES = {"notepad.exe": "记事本", "wechat.exe": "微信", "weixin.exe": "微信",
              "chrome.exe": "Chrome", "msedge.exe": "Microsoft Edge", "code.exe": "VS Code",
              "winword.exe": "Word", "explorer.exe": "文件资源管理器"}
_SCHEMA = """
CREATE TABLE IF NOT EXISTS dictations (
    id INTEGER PRIMARY KEY,
    created_at REAL NOT NULL,
    app_id TEXT NOT NULL,
    app_name TEXT NOT NULL,
    text TEXT NOT NULL,
    elapsed_ms INTEGER NOT NULL,
    path TEXT NOT NULL,
    provider TEXT,
    whisper TEXT NOT NULL,
    sensevoice TEXT NOT NULL,
    outcome TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS dictations_time ON dictations(created_at);
"""


@dataclass(frozen=True)
class Record:
    id: int
    created_at: float
    app_id: str
    app_name: str
    text: str
    elapsed_ms: int
    path: str
    provider: str | None
    whisper: str
    sensevoice: str
    outcome: str

    @property
    def route(self) -> str:
        return "已整理" if self.path == "llm" else "直出"


class HistoryStore:
    def __init__(self, path: Path, *, now: Callable[[], float] = time.time):
        self.path = path
        self.now = now

    @contextmanager
    def _connect(self):
        with closing(sqlite3.connect(self.path, timeout=0.5)) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA secure_delete = ON")
            db.executescript(_SCHEMA)
            with db:
                yield db

    def _prune(self, db, now: float):
        db.execute("DELETE FROM dictations WHERE created_at < ?", (now - RETENTION_S,))

    def prune(self) -> None:
        with self._connect() as db:
            self._prune(db, self.now())

    def add(self, result: Result, app: str, elapsed_ms: int, outcome: str) -> None:
        if not result.text.strip():
            return
        now = self.now()
        with self._connect() as db:
            self._prune(db, now)
            db.execute("""
                INSERT INTO dictations (created_at, app_id, app_name, text, elapsed_ms,
                                         path, provider, whisper, sensevoice, outcome)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (now, app, _APP_NAMES.get(app, app.removesuffix(".exe") or "未知应用"),
                  result.text, elapsed_ms, result.path, result.provider, result.a, result.b, outcome))

    def list(self, query: str = "", route: str = "all", *, limit: int = -1) -> list[Record]:
        query = query.strip().lower()
        with self._connect() as db:
            self._prune(db, self.now())
            rows = db.execute("""
                SELECT * FROM dictations
                WHERE (? = '' OR instr(lower(text), ?) OR instr(lower(app_name), ?) OR instr(lower(app_id), ?))
                  AND (? = 'all' OR (? = 'smart' AND path = 'llm') OR (? = 'raw' AND path <> 'llm'))
                ORDER BY created_at DESC, id DESC LIMIT ?
            """, (query, query, query, query, route, route, route, limit))
            return [Record(**row) for row in rows]
