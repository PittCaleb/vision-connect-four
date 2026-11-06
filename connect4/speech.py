"""Speak announcements aloud with macOS's `say` command, e.g. into an earbud."""

import shutil
import subprocess
import sys
import threading


class Speaker:
    """Speaks messages one at a time on a background thread.

    A message given a `replaces` tag drops any not-yet-spoken message with the
    same tag, so a stale move suggestion is never read out after a newer one.
    """

    def __init__(self, enabled: bool):
        self.enabled = enabled
        self._say = shutil.which("say") if enabled else None
        if enabled and not self._say:
            print("Audio: the macOS 'say' command wasn't found; printing announcements instead.",
                  file=sys.stderr)
        self._queue: list[tuple[str, str | None]] = []
        self._lock = threading.Condition()
        if self._say:
            threading.Thread(target=self._run, daemon=True).start()

    def say(self, text: str, replaces: str | None = None) -> None:
        if not self.enabled:
            return
        if not self._say:
            print(f"[audio] {text}")
            return
        with self._lock:
            if replaces:
                self._queue = [item for item in self._queue if item[1] != replaces]
            self._queue.append((text, replaces))
            self._lock.notify()

    def _run(self) -> None:
        while True:
            with self._lock:
                while not self._queue:
                    self._lock.wait()
                text, _ = self._queue.pop(0)
            subprocess.run([self._say, text], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, check=False)
