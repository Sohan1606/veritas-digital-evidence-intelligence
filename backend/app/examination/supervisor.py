"""The in-process worker loop. One daemon thread per backend process; no external queue.

Several processes (or a restarted one) are safe: every claim is a database compare-and-set, so
a run is executed by at most one worker at a time. The loop never lets an exception end the
thread: a database outage only delays work, and recovery returns interrupted runs to the queue.
"""

from __future__ import annotations

import logging
import threading

from app.examination.coordinator import RunCoordinator

logger = logging.getLogger("veritas.examination")


class ExaminationSupervisor:
    def __init__(self, coordinator: RunCoordinator, *, poll_seconds: float) -> None:
        self._coordinator = coordinator
        self._poll_seconds = poll_seconds
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="veritas-examination-worker", daemon=True
        )
        self._thread.start()

    def wake(self) -> None:
        """Ask the loop to look for work now instead of at the next poll."""
        self._wake.set()

    def stop(self, timeout: float = 20.0) -> bool:
        """Stop claiming, interrupt the run in flight (it is released to the queue) and join."""
        self._stop.set()
        self._wake.set()
        thread = self._thread
        if thread is None:
            return True
        thread.join(timeout)
        return not thread.is_alive()

    def _loop(self) -> None:
        logger.info("examination worker started", extra={"event": "examination_worker_started"})
        while not self._stop.is_set():
            worked = False
            try:
                self._coordinator.recover_stale()
                worked = self._coordinator.run_next(self._stop) is not None
            except Exception as exc:  # the loop must outlive any single failure
                logger.warning(
                    "examination worker iteration failed",
                    extra={
                        "event": "examination_worker_error",
                        "exception_type": type(exc).__name__,
                    },
                )
            if not worked and not self._stop.is_set():
                self._wake.wait(self._poll_seconds)
                self._wake.clear()
        logger.info("examination worker stopped", extra={"event": "examination_worker_stopped"})
