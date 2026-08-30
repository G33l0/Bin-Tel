"""Licensing: plans, entitlements, signed licences and device activation."""

from app.licensing.activation import (
    ActivationRejected,
    ActivationService,
    DeviceLimitReached,
    HttpLicenseClient,
    LicenseClient,
    LicenseError,
    LocalLicenseServer,
)
from app.licensing.devices import DeviceIdentity, DeviceManager
from app.licensing.entitlements import Entitlement, EntitlementService
from app.licensing.license_manager import LicenseManager
from app.licensing.models import (
    DeviceRecord,
    LicensePayload,
    LicenseSnapshot,
    LicenseState,
    LicenseToken,
    redact_key,
)
from app.licensing.plans import (
    DEFAULT_PLANS,
    UNLIMITED,
    Feature,
    Limit,
    Plan,
    PlanCatalogue,
    PlanDefinition,
    comparison_matrix,
)

__all__ = [
    "DEFAULT_PLANS",
    "UNLIMITED",
    "ActivationRejected",
    "ActivationService",
    "DeviceIdentity",
    "DeviceLimitReached",
    "DeviceManager",
    "DeviceRecord",
    "Entitlement",
    "EntitlementService",
    "Feature",
    "HttpLicenseClient",
    "LicenseClient",
    "LicenseError",
    "LicenseManager",
    "LicensePayload",
    "LicenseSnapshot",
    "LicenseState",
    "LicenseToken",
    "Limit",
    "LocalLicenseServer",
    "Plan",
    "PlanCatalogue",
    "PlanDefinition",
    "comparison_matrix",
    "redact_key",
]
