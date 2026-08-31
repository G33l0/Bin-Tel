# Analytics

Every figure on the Analytics page is computed from the database in front of
you, in one pass, and says what it measures.

---

## Headline figures

| Figure | Exactly what it counts |
|---|---|
| Total BINs | Rows in `bins` |
| Total Institutions | Rows in `institutions` |
| Total Countries | **Distinct countries BIN records point at** — coverage, not the size of the ISO reference table |
| Total Networks | **Distinct networks BIN records point at** — coverage, not the size of the network reference table |
| Credit / Debit / Prepaid / Commercial BINs | Rows matching that attribute, with their share of the total |

The coverage distinction matters: the database ships 249 ISO countries and 19
card networks as reference data, but a package may only have BINs in 31
countries across 8 networks. Reporting 249 would be a lie about coverage. The
dashboard, the Analytics page and the Administration page all count the same
way, so they agree.

---

## Distributions

By country, network, card type, funding type, currency and status. Each
distribution is a list of slices with a key, a label and a value; the top eight
are charted and the remainder collapse into "Other (n)". Every distribution sums
to the scoped total, so shares always add to 100%.

---

## Growth

Records first seen in each period, and the running total, from `first_seen`.
Where the database ships `database_versions` rows, releases are annotated on the
series so you can see which update brought what.

---

## Scope

The country, network and card-type filters at the top of the page scope
*everything* below them — headline figures, every distribution, growth, and the
top-institutions chart. The scope line under the filters always says what is
being shown, and the same `scope_condition` is shared with advanced search, so
"filtered to US, Visa" means the same thing on both pages.

---

## Caching

A snapshot is cached per (scope, database version). Changing the version, or
installing an update, invalidates it — `AnalyticsService.invalidate()` is called
from the post-install hook. Computation runs on a worker thread; the page shows
how long it took.

---

## Institution analytics

The Institution Intelligence page runs the same machinery scoped to one issuer:
its network mix, card-type mix and country footprint, computed from that
institution's BIN portfolio.

---

## Charts

Charts are painted with QPainter and read their colours from the active theme,
so they are correct in all five themes and need no external charting library.
Every chart has an accessible name, and bars and slices are clickable — clicking
an institution opens its profile.
