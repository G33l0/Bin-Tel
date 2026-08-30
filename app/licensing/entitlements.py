"""The entitlement service — the single place that answers "may I?".

No widget compares a plan name, and no page imports the licence manager to
decide what to render. Everything asks this service about a *named feature* or
a *named limit*, which is what keeps packaging changes out of the interface.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from app.core.logging_config import get_logger
from app.licensing.license_manager import LicenseManager
from app.licensing.plans import (
    UNLIMITED,
    Feature,
    Limit,
    Plan,
    PlanCatalogue,
    PlanDefinition,
)

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class Entitlement:
    """The answer to a feature question, with everything the UI needs to act."""

    feature: Feature
    granted: bool
    plan: Plan
    #: The cheapest plan that would grant it, when it is not granted.
    required_plan: Plan | None = None
    reason: str = ""

    @property
    def upgrade_label(self) -> str:
        if self.granted or self.required_plan is None:
            return ""
        return f"{self.required_plan.label} feature"

    def __bool__(self) -> bool:  # pragma: no cover - convenience
        return self.granted


class EntitlementService:
    """Resolves features and limits for the current licence."""

    def __init__(
        self,
        licenses: LicenseManager,
        catalogue: PlanCatalogue | None = None,
    ) -> None:
        self._licenses = licenses
        self._catalogue = catalogue or PlanCatalogue()
        self._listeners: list[Callable[[], None]] = []

    # -- catalogue --------------------------------------------------------
    @property
    def catalogue(self) -> PlanCatalogue:
        return self._catalogue

    def set_catalogue(self, catalogue: PlanCatalogue) -> None:
        self._catalogue = catalogue
        self.notify()

    @property
    def plan(self) -> Plan:
        return self._licenses.plan

    @property
    def definition(self) -> PlanDefinition:
        return self._catalogue.get(self.plan)

    # -- questions --------------------------------------------------------
    def has_feature(self, feature: str | Feature) -> bool:
        """Whether the current licence grants *feature*."""
        name = str(feature)
        snapshot = self._licenses.snapshot
        # An explicit grant in a signed licence overrides the plan matrix,
        # which is how bespoke enterprise entitlements work.
        if snapshot.state.is_entitled and snapshot.features:
            return name in snapshot.features
        return self.definition.has(name)

    def entitlement(self, feature: str | Feature) -> Entitlement:
        """The full answer, including which plan would grant the feature."""
        try:
            resolved = Feature(str(feature))
        except ValueError:
            resolved = Feature.BIN_LOOKUP
        granted = self.has_feature(feature)
        if granted:
            return Entitlement(feature=resolved, granted=True, plan=self.plan, reason="Included in your plan.")
        required = self._catalogue.plan_for_feature(feature)
        return Entitlement(
            feature=resolved,
            granted=False,
            plan=self.plan,
            required_plan=required.plan if required else Plan.PRO,
            reason=resolved.description or "",
        )

    def limit(self, limit: str | Limit, default: int = 0) -> int:
        """The numeric quota for *limit*; ``-1`` means unlimited."""
        snapshot = self._licenses.snapshot
        if snapshot.state.is_entitled and snapshot.limits:
            override = snapshot.limits.get(str(limit))
            if override is not None:
                return int(override)
        return self.definition.limit(limit, default)

    def is_unlimited(self, limit: str | Limit) -> bool:
        return self.limit(limit) == UNLIMITED

    def within_limit(self, limit: str | Limit, count: int) -> bool:
        """Whether *count* is allowed under *limit*."""
        value = self.limit(limit)
        return True if value == UNLIMITED else count <= value

    def remaining(self, limit: str | Limit, used: int) -> int | None:
        """How much of a quota is left; ``None`` when unlimited."""
        value = self.limit(limit)
        return None if value == UNLIMITED else max(0, value - used)

    def cap(self, limit: str | Limit, requested: int) -> int:
        """Clamp a requested count to the licensed quota."""
        value = self.limit(limit)
        return requested if value == UNLIMITED else min(requested, max(0, value))

    def limit_description(self, limit: str | Limit) -> str:
        value = self.limit(limit)
        return "Unlimited" if value == UNLIMITED else f"{value:,}"

    @property
    def database_edition(self) -> str:
        """Which database edition this licence is entitled to receive."""
        snapshot = self._licenses.snapshot
        if snapshot.state.is_entitled and snapshot.edition:
            return snapshot.edition
        return self.definition.database_edition

    # -- change propagation ------------------------------------------------
    def subscribe(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Register a callback fired whenever entitlements may have changed."""
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    def notify(self) -> None:
        for listener in list(self._listeners):
            try:
                listener()
            except Exception:  # noqa: BLE001 - a listener must not break licensing
                logger.exception("An entitlement listener raised")

    # -- diagnostics -------------------------------------------------------
    def summary(self) -> dict[str, object]:
        """Non-identifying summary, used by telemetry and the About page."""
        return {
            "plan": self.plan.value,
            "state": self._licenses.state.value,
            "edition": self.database_edition,
            "features": sorted(
                feature.value for feature in Feature if self.has_feature(feature)
            ),
        }
