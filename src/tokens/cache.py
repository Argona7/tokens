"""Incremental file cache for the ~10k Claude JSONL files.

A cold parse of every session file is slow, so we memoise each file's aggregated
result keyed by (path, mtime, size). On the next run only changed/new files are
re-parsed; everything else is read back from SQLite. The per-file payload is a
small JSON blob, so summing thousands of them in Python is fast.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Callable

from .config import cache_dir


class FileCache:
    def __init__(self, name: str, version: int = 1):
        # version is part of the filename so a parser change invalidates old data.
        self.path = cache_dir() / f"{name}.v{version}.db"
        self.conn = sqlite3.connect(str(self.path))
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS files("
            "path TEXT PRIMARY KEY, mtime REAL, size INTEGER, data TEXT)"
        )
        self.conn.commit()

    def get(self, path: Path) -> dict | None:
        try:
            st = path.stat()
        except OSError:
            return None
        row = self.conn.execute(
            "SELECT mtime, size, data FROM files WHERE path=?", (str(path),)
        ).fetchone()
        if row and row[0] == st.st_mtime and row[1] == st.st_size:
            try:
                return json.loads(row[2])
            except (json.JSONDecodeError, ValueError):
                return None
        return None

    def put(self, path: Path, data: dict) -> None:
        try:
            st = path.stat()
        except OSError:
            return
        self.conn.execute(
            "INSERT OR REPLACE INTO files(path, mtime, size, data) VALUES (?,?,?,?)",
            (str(path), st.st_mtime, st.st_size, json.dumps(data, separators=(",", ":"))),
        )

    def commit(self) -> None:
        self.conn.commit()

    def process(
        self,
        files: list[Path],
        parse: Callable[[Path], dict],
        on_progress: Callable[[int, int, int], None] | None = None,
    ) -> list[dict]:
        """Return parsed payloads for every file, using cache where fresh.

        `parse(path) -> dict` is only called for new/changed files. `on_progress`
        receives (done, total, parsed_this_run) so the CLI can show a cold-build bar.
        """
        out: list[dict] = []
        total = len(files)
        parsed = 0
        for i, f in enumerate(files, 1):
            cached = self.get(f)
            if cached is None:
                try:
                    cached = parse(f)
                except Exception:
                    cached = {}
                self.put(f, cached)
                parsed += 1
            out.append(cached)
            if on_progress and (i % 64 == 0 or i == total):
                on_progress(i, total, parsed)
        if parsed:
            self.commit()
        return out
