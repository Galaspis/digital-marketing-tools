"""Background thread: publishes locally scheduled posts when due and checks
on platform-scheduled ones. Runs only while the API server runs, which is
why Facebook posts use the platform's own scheduling whenever possible."""

from __future__ import annotations

import threading
import time

from .publisher import Publisher, PublishError


class Scheduler(threading.Thread):
    def __init__(self, publisher: Publisher, guard, interval: float = 20.0) -> None:
        super().__init__(daemon=True, name="social-scheduler")
        self.publisher = publisher
        self.guard = guard
        self.interval = interval
        self._stop = threading.Event()

    def tick(self) -> int:
        """One pass. Returns how many posts were published."""
        n = 0
        for post in self.publisher.store.due():
            try:
                result = self.publisher.publish_now(post["id"], source="scheduler")
            except PublishError as e:
                self.publisher.store.update(post["id"], status="failed", error=str(e))
                self.guard.log("scheduled_publish", post["target_id"], {"post_id": post["id"]}, approved=None, note=str(e))
                continue
            ok = result["status"] == "published"
            self.guard.log("scheduled_publish", post["target_id"], {"post_id": post["id"]},
                           approved=None, note="published" if ok else result.get("error"))
            n += int(ok)
        try:
            self.publisher.refresh_native()
        except Exception as e:  # never let a status check kill the loop
            self.guard.log("refresh_native", None, {}, approved=None, note=f"{type(e).__name__}: {e}")
        return n

    def run(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                self.tick()
            except Exception as e:
                self.guard.log("scheduler", None, {}, approved=None, note=f"{type(e).__name__}: {e}")

    def stop(self) -> None:
        self._stop.set()
