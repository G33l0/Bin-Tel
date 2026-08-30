"""Delta (incremental) database updates.

The distribution format already advertises deltas in the manifest, and the
update pipeline asks this module whether one can be used before it falls back
to a full download. No delta format is implemented yet — this build always
takes the full package — but the decision point, the interface an applier must
satisfy, and the registry that selects one are all in place, so introducing a
format later is a matter of registering an applier rather than reworking the
update pipeline.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from app.core.logging_config import get_logger
from app.providers.manifest import DatabaseManifest, DeltaDescriptor

logger = get_logger(__name__)

ProgressCallback = Callable[[int, int], None]
CancelCheck = Callable[[], bool]


@dataclass(frozen=True, slots=True)
class DeltaPlan:
    """The chosen strategy for reaching a target version."""

    #: True when a delta can be applied instead of a full download.
    use_delta: bool
    descriptor: DeltaDescriptor | None = None
    reason: str = ""

    @property
    def bytes_saved(self) -> int:
        return 0 if not self.descriptor else max(0, self.descriptor.size)


class DeltaApplier(ABC):
    """Applies a downloaded delta to a base database, producing the target."""

    #: Manifest ``format`` value this applier handles.
    format: str = "abstract"

    @abstractmethod
    def apply(
        self,
        base: Path,
        delta: Path,
        destination: Path,
        *,
        progress: ProgressCallback | None = None,
        cancelled: CancelCheck | None = None,
    ) -> Path:
        """Produce *destination* from *base* plus *delta*.

        Must never modify *base*: the update pipeline still has to be able to
        fall back to the current database if verification later fails.
        """

    def can_apply(self, descriptor: DeltaDescriptor) -> bool:
        return descriptor.format == self.format


#: Registered appliers, newest registration wins for a given format.
_APPLIERS: list[DeltaApplier] = []


def register_applier(applier: DeltaApplier) -> DeltaApplier:
    """Register an applier so :func:`plan_update` can select it."""
    _APPLIERS.insert(0, applier)
    logger.info("Delta applier registered", extra={"context": {"format": applier.format}})
    return applier


def applier_for(descriptor: DeltaDescriptor) -> DeltaApplier | None:
    return next((item for item in _APPLIERS if item.can_apply(descriptor)), None)


def supported_formats() -> list[str]:
    return [item.format for item in _APPLIERS]


def plan_update(
    manifest: DatabaseManifest,
    current_version: str | None,
    *,
    allow_delta: bool = True,
) -> DeltaPlan:
    """Decide between a delta and a full download.

    Returning ``use_delta=False`` is the normal outcome today; the *reason* is
    surfaced in the update log so the choice is never silent.
    """
    if not allow_delta:
        return DeltaPlan(use_delta=False, reason="Delta updates are disabled.")
    if not current_version:
        return DeltaPlan(use_delta=False, reason="No database is installed yet.")
    if not manifest.has_deltas:
        return DeltaPlan(use_delta=False, reason="The release publishes no deltas.")

    descriptor = manifest.delta_from(current_version)
    if descriptor is None:
        return DeltaPlan(
            use_delta=False,
            reason=f"No delta published from {current_version} to {manifest.version}.",
        )
    applier = applier_for(descriptor)
    if applier is None:
        return DeltaPlan(
            use_delta=False,
            descriptor=descriptor,
            reason=(
                f"A {descriptor.format} delta is available, but this build has no "
                "applier for that format, so the full database will be downloaded."
            ),
        )
    if descriptor.size and manifest.transfer_size and descriptor.size >= manifest.transfer_size:
        return DeltaPlan(
            use_delta=False,
            descriptor=descriptor,
            reason="The delta is no smaller than the full package.",
        )
    return DeltaPlan(use_delta=True, descriptor=descriptor, reason="A usable delta is published.")
