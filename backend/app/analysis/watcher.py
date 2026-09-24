"""Watches MUSIC_DIR and triggers a scan when files are added, removed or changed.

Polling (no extra dependency, works on network drives). A change only triggers a scan once the
folder has been stable for one interval, so half-downloaded files are not analysed.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path

from app.analysis.scanner import find_audio

log = logging.getLogger("jevdj.watcher")

Snapshot = dict[str, tuple[int, float]]


def snapshot(root: Path) -> Snapshot:
    out: Snapshot = {}
    for p in find_audio(root):
        try:
            st = p.stat()
            out[str(p)] = (st.st_size, st.st_mtime)
        except OSError:
            pass  # file vanished mid-listing
    return out


class LibraryWatcher(threading.Thread):
    def __init__(self, root: Path, on_change: Callable[[Snapshot, Snapshot], bool], interval: float = 5.0) -> None:
        """on_change(old, new) returns True if it handled the change (e.g. a scan started)."""
        super().__init__(daemon=True, name="library-watcher")
        self.root = root
        self.on_change = on_change
        self.interval = interval
        self._stop = threading.Event()
        self.handled: Snapshot = {}

    def mark_handled(self, snap: Snapshot | None = None) -> None:
        self.handled = snap if snap is not None else snapshot(self.root)

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        prev = snapshot(self.root)
        while not self._stop.wait(self.interval):
            try:
                cur = snapshot(self.root)
            except Exception as exc:  # e.g. drive unplugged
                log.warning("watch failed: %s", exc)
                continue
            if cur == prev and cur != self.handled:
                if self.on_change(self.handled, cur):
                    self.handled = cur
            prev = cur
