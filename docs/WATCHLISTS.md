# Watchlists

A watchlist answers a question the database alone cannot: *what changed?*

Add a BIN, an institution or a country to a watchlist and Bin-Tel captures its
current state as a baseline. After the next database update it compares the new
release against that baseline and tells you exactly what moved.

---

## How it works

**On add.** `ChangeDetectionService.snapshot()` reads the target's comparable
state from the live database and stores it as JSON on the watchlist item, with
the database version it came from.

**On update.** The install pipeline's post-install hook calls
`WatchlistService.scan_for_changes()`. Each watched target is re-snapshotted and
compared field by field. Differences become `watchlist_events`; the stored
snapshot is refreshed in the same pass, so the *next* update compares against
this release and the same change is never reported twice.

Because watchlists live in the user-data store and reference their targets by
value — BIN digits, institution `uid`, ISO country code — replacing the whole
intelligence database does not disturb them.

---

## What is compared

**BINs**: issuer, network, card type, funding type, country, status, city,
region, postal code.

**Institutions**: display name, legal name, country, website, status, city,
region, and how many BINs they hold.

**Countries**: BIN count, institution count, and the network mix.

Fields that are presentational or volatile — a marketing brand name, a
timestamp — are deliberately not compared, so an alert always means something.

---

## Alerts

| Change type | Severity |
|---|---|
| `bin_removed`, `institution_removed` | warning |
| `bin_added`, `institution_added` | info |
| `institution_changed`, `network_changed`, `card_type_changed`, `funding_type_changed`, `status_changed`, `country_changed`, `location_changed` | info |

Each event records the field, the previous value, the current value, the
versions it moved between, and when it was detected. Unread counts appear as a
badge on the Watchlists entry in the sidebar. Marking read is explicit; nothing
is silently dismissed.

Per-watchlist notification can be turned off without deleting the watchlist.

---

## Using them

Add from anywhere a record is shown — the BIN result card and the institution
profile both have an "Add to watchlist" action. Or use the Watchlists page
directly.

The Watchlists page lists your watchlists on the left and, for the selected one,
its watched items and detected changes on the right. "Check for changes now"
runs a scan on demand rather than waiting for an update; "Export activity"
writes the change log out as a report.

---

## Limits

Watchlists are a Pro feature. Free plans see the page with an upgrade prompt
rather than a hidden menu entry. Pro allows 5 watchlists, Business 50,
Enterprise unlimited; per-watchlist item counts are capped the same way. Limits
come from `Limit.WATCHLISTS` and `Limit.WATCHLIST_ITEMS` via the entitlement
service — see [MONETIZATION.md](MONETIZATION.md).

---

## Retention

Events are pruned on startup according to `watchlists.keep_events_days` (180 by
default). Deleting a watchlist deletes its items and its events with it.
