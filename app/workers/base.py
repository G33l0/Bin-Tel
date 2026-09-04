"""Worker foundations.

A worker owns one unit of blocking work. It never touches a widget: results and
errors travel back as signals, which Qt delivers on the GUI thread.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any, Generic, TypeVar

from PyQt6.QtCore import QObject, QRunnable, QThreadPool, pyqtSignal

from app.core.errors import OperationCancelled, friendly_message
from app.core.logging_config import get_logger

logger = get_logger(__name__)

T = TypeVar("T")


class CancellationToken:
    """Thread-safe cancellation flag polled by long-running operations."""

    __slots__ = ("_event",)

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    def reset(self) -> None:
        self._event.clear()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def __call__(self) -> bool:
        """Usable directly as the ``cancelled`` callback services expect."""
        return self._event.is_set()

    def raise_if_cancelled(self) -> None:
        if self._event.is_set():
            raise OperationCancelled("The operation was cancelled.")


class WorkerSignals(QObject):
    """The signal set every worker exposes.

    ``result`` carries a plain value (a DTO, a dataclass) — never an ORM object
    bound to a session that has since closed.
    """

    started = pyqtSignal()
    result = pyqtSignal(object)
    progress = pyqtSignal(object)
    message = pyqtSignal(str)
    failed = pyqtSignal(object)  # the exception instance
    error_text = pyqtSignal(str)
    cancelled = pyqtSignal()
    finished = pyqtSignal()


class Worker(QRunnable, Generic[T]):
    """Runs ``callable(*args, **kwargs)`` on the thread pool."""

    def __init__(self, function: Callable[..., T], *args: Any, **kwargs: Any) -> None:
        super().__init__()
        self.signals = WorkerSignals()
        self.token = CancellationToken()
        self._function = function
        self._args = args
        self._kwargs = kwargs
        self.setAutoDelete(True)

    def cancel(self) -> None:
        self.token.cancel()

    def _emit(self, signal: Any, *args: Any) -> None:
        """Emit unless the receiving object is already gone.

        On the way out Qt destroys its objects while pool threads may still be
        finishing. Emitting into a destroyed ``WorkerSignals`` raises from a
        thread with nowhere to hand the exception, and Qt turns that into an
        abort — the application dies on quit instead of closing. The work is
        over by then and nobody is listening, so dropping the signal is the
        whole of the harm.

        ``shutdown_workers`` makes this rare by draining the pool first; this
        makes it harmless when the race is lost anyway.
        """
        try:
            signal.emit(*args)
        except RuntimeError:
            logger.debug("Dropped a signal from a worker whose receiver is gone")

    def run(self) -> None:
        self._emit(self.signals.started)
        try:
            if self.token.cancelled:
                raise OperationCancelled("The operation was cancelled before it started.")
            value = self._function(*self._args, **self._kwargs)
        except OperationCancelled:
            self._emit(self.signals.cancelled)
        except Exception as exc:
            logger.exception("Background task failed: %s", type(exc).__name__)
            self._emit(self.signals.failed, exc)
            self._emit(self.signals.error_text, friendly_message(exc))
        else:
            self._emit(self.signals.result, value)
        finally:
            self._emit(self.signals.finished)


#: Workers currently on the pool.
#:
#: ``QThreadPool.start`` keeps a C++ reference to the runnable but not a Python
#: one, so a caller that does not store the worker lets Python collect it
#: mid-run — which destroys the ``WorkerSignals`` QObject underneath the thread
#: and turns every ``emit`` into "wrapped C/C++ object has been deleted". Every
#: call site holding its own reference would work; one set here means none of
#: them has to remember.
_IN_FLIGHT: set[Worker[Any]] = set()


def run_in_background(
    worker: Worker[Any], pool: QThreadPool | None = None
) -> Worker[Any]:
    """Submit *worker* to a thread pool and return it (for cancellation).

    The worker is kept alive until it reports that it has finished.
    """
    _IN_FLIGHT.add(worker)
    worker.signals.finished.connect(lambda: _IN_FLIGHT.discard(worker))
    (pool or QThreadPool.globalInstance()).start(worker)
    return worker


def shutdown_workers(timeout_ms: int = 5_000, pool: QThreadPool | None = None) -> bool:
    """Cancel what is still running and wait for the pool to drain.

    Called before the application tears its objects down. Without it a worker
    can still be running when Qt destroys the objects it reports to, which ends
    the process with an abort rather than a clean quit.

    Cancellation is cooperative — a task that never polls its token is waited
    for, not killed — so this returns whether the pool actually drained inside
    *timeout_ms*, and the caller can decide whether to care.
    """
    for worker in list(_IN_FLIGHT):
        worker.cancel()
    drained = (pool or QThreadPool.globalInstance()).waitForDone(timeout_ms)
    if not drained:
        logger.warning(
            "Background work did not finish within %d ms of shutdown", timeout_ms
        )
    _IN_FLIGHT.clear()
    return drained
    return worker


def in_flight_count() -> int:
    """How many workers are still running. Used by tests to wait for quiet."""
    return len(_IN_FLIGHT)
