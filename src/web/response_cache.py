"""Stamp-keyed in-process cache for read-heavy JSON endpoints.

Key = (st_mtime_ns, st_size, st_ino) of the SQLite file. Any commit from any
connection rewrites the file, so entries invalidate exactly when the data
changes — no TTL and no explicit invalidation hooks needed.
"""

import os
import threading


class DBStampCache:
    def __init__(self, max_entries: int = 8):
        self._max = max_entries
        self._lock = threading.Lock()
        self._entries: dict = {}
        self._order: list = []

    @staticmethod
    def stamp(db_path: str):
        try:
            st = os.stat(db_path)
        except OSError:
            return None
        return (st.st_mtime_ns, st.st_size, st.st_ino)

    def get(self, stamp):
        if stamp is None:
            return None
        with self._lock:
            return self._entries.get(stamp)

    def put(self, stamp, body: str) -> None:
        if stamp is None:
            return
        with self._lock:
            if stamp not in self._entries:
                self._order.append(stamp)
                while len(self._order) > self._max:
                    self._entries.pop(self._order.pop(0), None)
            self._entries[stamp] = body
