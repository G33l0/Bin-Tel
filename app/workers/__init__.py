"""Background workers.

Nothing that can block — a query, a download, a verification, an import — runs
on the GUI thread. Every worker here is a :class:`QRunnable` executed on a
:class:`QThreadPool` and communicates back through queued signals only.
"""

from app.workers.base import CancellationToken, Worker, WorkerSignals, run_in_background
from app.workers.maintenance_worker import (
    BackupWorker,
    ImportWorker,
    RestoreWorker,
    VerifyWorker,
)
from app.workers.search_worker import BankSearchWorker, BinPageWorker, BinSearchWorker
from app.workers.update_worker import UpdateCheckWorker, UpdateInstallWorker

__all__ = [
    "BackupWorker",
    "BankSearchWorker",
    "BinPageWorker",
    "BinSearchWorker",
    "CancellationToken",
    "ImportWorker",
    "RestoreWorker",
    "UpdateCheckWorker",
    "UpdateInstallWorker",
    "VerifyWorker",
    "Worker",
    "WorkerSignals",
    "run_in_background",
]
