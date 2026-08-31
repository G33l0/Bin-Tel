"""The plan catalogue and the feature matrix.

Plans are *data*, not code. The definitions below are the shipped defaults;
``config/plans.json`` overrides them, and a licensing service may hand down a
different entitlement set with a signed license. Nothing in the interface
compares against a plan name — everything asks the entitlement service about a
named feature or limit, so pricing and packaging can change without touching a
single widget.

Prices are display strings only. The desktop application never processes a
payment; see :mod:`app.services.billing_service`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from app.core.logging_config import get_logger

logger = get_logger(__name__)


class Plan(StrEnum):
    FREE = "free"
    PRO = "pro"
    BUSINESS = "business"
    ENTERPRISE = "enterprise"

    @property
    def label(self) -> str:
        return "Free" if self is Plan.FREE else self.value.capitalize()

    @property
    def rank(self) -> int:
        return {Plan.FREE: 0, Plan.PRO: 1, Plan.BUSINESS: 2, Plan.ENTERPRISE: 3}[self]

    def at_least(self, other: Plan) -> bool:
        return self.rank >= other.rank

    @classmethod
    def parse(cls, value: str | None) -> Plan:
        try:
            return cls(str(value or "free").strip().lower())
        except ValueError:
            return cls.FREE


class Feature(StrEnum):
    """Every gated capability, named once.

    The string values are the stable contract between the interface, the
    entitlement service and any license a server issues.
    """

    # Core lookup — never gated, listed so the matrix is complete and legible.
    BIN_LOOKUP = "bin_lookup"
    INSTITUTION_LOOKUP = "institution_lookup"
    BASIC_FILTERS = "basic_filters"
    BASIC_EXPORT = "basic_export"
    DATABASE_UPDATES = "database_updates"

    # Professional
    ADVANCED_SEARCH = "advanced_search"
    SAVED_SEARCHES = "saved_searches"
    FAVORITES = "favorites"
    WATCHLISTS = "watchlists"
    CHANGE_ALERTS = "change_alerts"
    HISTORY = "history"
    INSTITUTION_INTELLIGENCE = "institution_intelligence"
    ADVANCED_ANALYTICS = "advanced_analytics"
    PDF_REPORTS = "pdf_reports"
    XLSX_REPORTS = "xlsx_reports"
    REPORT_TEMPLATES = "report_templates"
    BATCH_LOOKUP = "batch_lookup"
    UNLIMITED_EXPORT = "unlimited_export"

    # Business and above
    API_ACCESS = "api_access"
    AUTOMATED_REPORTS = "automated_reports"
    TEAM_SHARING = "team_sharing"
    PRIORITY_DATABASE = "priority_database"

    # Enterprise
    CUSTOM_DEPLOYMENT = "custom_deployment"
    ENTERPRISE_API = "enterprise_api"
    DEDICATED_SUPPORT = "dedicated_support"

    @property
    def label(self) -> str:
        return _FEATURE_LABELS.get(self, self.value.replace("_", " ").title())

    @property
    def description(self) -> str:
        return _FEATURE_DESCRIPTIONS.get(self, "")


_FEATURE_LABELS: dict[Feature, str] = {
    Feature.BIN_LOOKUP: "BIN / IIN lookup",
    Feature.INSTITUTION_LOOKUP: "Institution lookup",
    Feature.BASIC_FILTERS: "Country, network and card filters",
    Feature.BASIC_EXPORT: "Export results",
    Feature.DATABASE_UPDATES: "Database updates",
    Feature.ADVANCED_SEARCH: "Advanced multi-criteria search",
    Feature.SAVED_SEARCHES: "Saved searches",
    Feature.FAVORITES: "Favourites",
    Feature.WATCHLISTS: "Watchlists",
    Feature.CHANGE_ALERTS: "Change alerts",
    Feature.HISTORY: "BIN and institution history",
    Feature.INSTITUTION_INTELLIGENCE: "Institution intelligence profiles",
    Feature.ADVANCED_ANALYTICS: "Advanced analytics",
    Feature.PDF_REPORTS: "PDF reports",
    Feature.XLSX_REPORTS: "Excel reports",
    Feature.REPORT_TEMPLATES: "Saved report templates",
    Feature.BATCH_LOOKUP: "Batch lookup",
    Feature.UNLIMITED_EXPORT: "Unlimited exports",
    Feature.API_ACCESS: "API access",
    Feature.AUTOMATED_REPORTS: "Scheduled reports",
    Feature.TEAM_SHARING: "Team sharing",
    Feature.PRIORITY_DATABASE: "Priority database edition",
    Feature.CUSTOM_DEPLOYMENT: "Custom deployment",
    Feature.ENTERPRISE_API: "Enterprise API",
    Feature.DEDICATED_SUPPORT: "Dedicated support",
}

_FEATURE_DESCRIPTIONS: dict[Feature, str] = {
    Feature.ADVANCED_SEARCH: (
        "Combine BIN ranges, institutions, geography, networks and card attributes "
        "in a single query."
    ),
    Feature.SAVED_SEARCHES: "Keep a query and re-run it whenever the database changes.",
    Feature.WATCHLISTS: (
        "Track specific BINs, institutions and countries, and see exactly what a "
        "database update changed about them."
    ),
    Feature.CHANGE_ALERTS: "Be told when a watched record changes after an update.",
    Feature.HISTORY: "See how a BIN or institution record changed across releases.",
    Feature.ADVANCED_ANALYTICS: (
        "Distribution, coverage and growth analysis across the whole database."
    ),
    Feature.PDF_REPORTS: "Branded, presentation-ready PDF reports.",
    Feature.XLSX_REPORTS: "Excel workbooks with formatted, filterable result tables.",
    Feature.REPORT_TEMPLATES: "Save a report definition and reuse it.",
    Feature.BATCH_LOOKUP: "Resolve many BINs in one pass from a file or a pasted list.",
    Feature.API_ACCESS: "Programmatic access for your own systems.",
}


class Limit(StrEnum):
    """Numeric quotas. ``-1`` means unlimited."""

    EXPORT_ROWS = "export_rows"
    BATCH_SIZE = "batch_size"
    SAVED_SEARCHES = "saved_searches"
    WATCHLISTS = "watchlists"
    WATCHLIST_ITEMS = "watchlist_items"
    REPORT_TEMPLATES = "report_templates"
    DEVICES = "devices"

    @property
    def label(self) -> str:
        return self.value.replace("_", " ").title()


UNLIMITED = -1


@dataclass(frozen=True, slots=True)
class PlanDefinition:
    """One purchasable tier."""

    plan: Plan
    name: str
    tagline: str
    price_display: str
    billing_note: str
    features: frozenset[str]
    limits: dict[str, int]
    highlights: tuple[str, ...] = ()
    database_edition: str = "community"
    popular: bool = False

    def has(self, feature: str | Feature) -> bool:
        return str(feature) in self.features

    def limit(self, limit: str | Limit, default: int = 0) -> int:
        return self.limits.get(str(limit), default)

    @property
    def is_free(self) -> bool:
        return self.plan is Plan.FREE


#: Features every plan gets. The free tier is a useful product, not a demo:
#: the whole local database, every lookup, filters and exports are included.
_BASE_FEATURES: frozenset[str] = frozenset(
    {
        Feature.BIN_LOOKUP.value,
        Feature.INSTITUTION_LOOKUP.value,
        Feature.BASIC_FILTERS.value,
        Feature.BASIC_EXPORT.value,
        Feature.DATABASE_UPDATES.value,
    }
)

_PRO_FEATURES: frozenset[str] = _BASE_FEATURES | {
    Feature.ADVANCED_SEARCH.value,
    Feature.SAVED_SEARCHES.value,
    Feature.FAVORITES.value,
    Feature.WATCHLISTS.value,
    Feature.CHANGE_ALERTS.value,
    Feature.HISTORY.value,
    Feature.INSTITUTION_INTELLIGENCE.value,
    Feature.ADVANCED_ANALYTICS.value,
    Feature.PDF_REPORTS.value,
    Feature.XLSX_REPORTS.value,
    Feature.REPORT_TEMPLATES.value,
    Feature.BATCH_LOOKUP.value,
    Feature.UNLIMITED_EXPORT.value,
}

_BUSINESS_FEATURES: frozenset[str] = _PRO_FEATURES | {
    Feature.API_ACCESS.value,
    Feature.AUTOMATED_REPORTS.value,
    Feature.TEAM_SHARING.value,
    Feature.PRIORITY_DATABASE.value,
}

_ENTERPRISE_FEATURES: frozenset[str] = _BUSINESS_FEATURES | {
    Feature.CUSTOM_DEPLOYMENT.value,
    Feature.ENTERPRISE_API.value,
    Feature.DEDICATED_SUPPORT.value,
}


DEFAULT_PLANS: tuple[PlanDefinition, ...] = (
    PlanDefinition(
        plan=Plan.FREE,
        name="Free",
        tagline="The complete local database and unlimited lookups.",
        price_display="Free",
        billing_note="No account required.",
        features=_BASE_FEATURES,
        limits={
            Limit.EXPORT_ROWS.value: 500,
            Limit.BATCH_SIZE.value: 0,
            Limit.SAVED_SEARCHES.value: 0,
            Limit.WATCHLISTS.value: 0,
            Limit.WATCHLIST_ITEMS.value: 0,
            Limit.REPORT_TEMPLATES.value: 0,
            Limit.DEVICES.value: 1,
        },
        highlights=(
            "Unlimited offline BIN and institution lookups",
            "The full local intelligence database",
            "Country, network, card-type and funding filters",
            "CSV, JSON and text exports up to 500 rows",
            "Free database updates",
        ),
        database_edition="community",
    ),
    PlanDefinition(
        plan=Plan.PRO,
        name="Pro",
        tagline="For professionals who work with issuer data every day.",
        price_display="$12 / month",
        billing_note="Billed annually or monthly. Cancel at any time.",
        features=_PRO_FEATURES,
        limits={
            Limit.EXPORT_ROWS.value: UNLIMITED,
            Limit.BATCH_SIZE.value: 5_000,
            Limit.SAVED_SEARCHES.value: 100,
            Limit.WATCHLISTS.value: 25,
            Limit.WATCHLIST_ITEMS.value: 2_000,
            Limit.REPORT_TEMPLATES.value: 50,
            Limit.DEVICES.value: 3,
        },
        highlights=(
            "Everything in Free",
            "Advanced multi-criteria search and saved searches",
            "Watchlists with change alerts after every database update",
            "BIN and institution history",
            "Institution intelligence profiles and advanced analytics",
            "PDF and Excel reports with saved templates",
            "Batch lookup up to 5,000 BINs",
        ),
        database_edition="professional",
        popular=True,
    ),
    PlanDefinition(
        plan=Plan.BUSINESS,
        name="Business",
        tagline="For teams that build issuer data into their own systems.",
        price_display="$49 / month",
        billing_note="Per workspace. Volume pricing available.",
        features=_BUSINESS_FEATURES,
        limits={
            Limit.EXPORT_ROWS.value: UNLIMITED,
            Limit.BATCH_SIZE.value: 100_000,
            Limit.SAVED_SEARCHES.value: UNLIMITED,
            Limit.WATCHLISTS.value: UNLIMITED,
            Limit.WATCHLIST_ITEMS.value: UNLIMITED,
            Limit.REPORT_TEMPLATES.value: UNLIMITED,
            Limit.DEVICES.value: 10,
        },
        highlights=(
            "Everything in Pro",
            "Batch lookup up to 100,000 BINs",
            "API access for your own systems",
            "Scheduled and automated reports",
            "Shared watchlists and report templates",
            "Business database edition",
            "Up to 10 activated devices",
        ),
        database_edition="business",
    ),
    PlanDefinition(
        plan=Plan.ENTERPRISE,
        name="Enterprise",
        tagline="High-volume access, custom deployment and dedicated support.",
        price_display="Custom",
        billing_note="Talk to us about your requirements.",
        features=_ENTERPRISE_FEATURES,
        limits={
            Limit.EXPORT_ROWS.value: UNLIMITED,
            Limit.BATCH_SIZE.value: UNLIMITED,
            Limit.SAVED_SEARCHES.value: UNLIMITED,
            Limit.WATCHLISTS.value: UNLIMITED,
            Limit.WATCHLIST_ITEMS.value: UNLIMITED,
            Limit.REPORT_TEMPLATES.value: UNLIMITED,
            Limit.DEVICES.value: UNLIMITED,
        },
        highlights=(
            "Everything in Business",
            "Enterprise API with high-volume limits",
            "Custom and on-premises deployment",
            "Enterprise database edition",
            "Dedicated support and onboarding",
            "Unlimited devices",
        ),
        database_edition="enterprise",
    ),
)


@dataclass(slots=True)
class PlanCatalogue:
    """The set of plans this build offers, overridable from configuration."""

    plans: list[PlanDefinition] = field(
        default_factory=lambda: list(DEFAULT_PLANS)
    )

    def get(self, plan: Plan | str) -> PlanDefinition:
        wanted = Plan.parse(str(plan))
        for definition in self.plans:
            if definition.plan is wanted:
                return definition
        return self.plans[0]

    def ordered(self) -> list[PlanDefinition]:
        return sorted(self.plans, key=lambda item: item.plan.rank)

    def paid(self) -> list[PlanDefinition]:
        return [item for item in self.ordered() if not item.is_free]

    def plan_for_feature(self, feature: str | Feature) -> PlanDefinition | None:
        """The cheapest plan that includes *feature*."""
        for definition in self.ordered():
            if definition.has(feature):
                return definition
        return None

    @classmethod
    def load(cls, path: Path | None = None) -> PlanCatalogue:
        """Load ``config/plans.json`` if present, else the shipped defaults."""
        if path is None or not path.exists():
            return cls()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Plan catalogue could not be read (%s); using defaults", exc)
            return cls()
        entries = raw.get("plans") if isinstance(raw, dict) else raw
        if not isinstance(entries, list):
            return cls()

        defaults = {item.plan: item for item in DEFAULT_PLANS}
        plans: list[PlanDefinition] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            definition = _definition_from(entry, defaults)
            if definition is not None:
                plans.append(definition)
        if not plans:
            return cls()
        logger.info(
            "Plan catalogue loaded from configuration",
            extra={"context": {"plans": [item.plan.value for item in plans]}},
        )
        return cls(plans=plans)


def _definition_from(
    entry: dict[str, Any], defaults: dict[Plan, PlanDefinition]
) -> PlanDefinition | None:
    plan = Plan.parse(entry.get("plan"))
    base = defaults.get(plan)
    if base is None:
        return None
    features = entry.get("features")
    limits = entry.get("limits")
    return PlanDefinition(
        plan=plan,
        name=str(entry.get("name", base.name)),
        tagline=str(entry.get("tagline", base.tagline)),
        price_display=str(entry.get("price_display", base.price_display)),
        billing_note=str(entry.get("billing_note", base.billing_note)),
        features=frozenset(str(item) for item in features)
        if isinstance(features, list)
        else base.features,
        limits={str(k): int(v) for k, v in limits.items()}
        if isinstance(limits, dict)
        else dict(base.limits),
        highlights=tuple(str(item) for item in entry.get("highlights", base.highlights)),
        database_edition=str(entry.get("database_edition", base.database_edition)),
        popular=bool(entry.get("popular", base.popular)),
    )


def comparison_matrix(catalogue: PlanCatalogue) -> list[tuple[Feature, dict[Plan, bool]]]:
    """Rows for the plan-comparison dialog."""
    rows: list[tuple[Feature, dict[Plan, bool]]] = []
    for feature in Feature:
        rows.append(
            (feature, {item.plan: item.has(feature) for item in catalogue.ordered()})
        )
    return rows
