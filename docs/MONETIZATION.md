# Plans and entitlements

## The rule

**No widget compares a plan name.** Every gated surface asks the entitlement
service about a *named feature* or a *named limit*:

```python
if self.context.entitlements.has_feature(Feature.ADVANCED_SEARCH):
    ...

cap = self.context.entitlements.limit(Limit.EXPORT_ROWS, 500)
```

That indirection is what lets packaging and pricing change without touching a
single page. Grep the interface for `Plan.PRO` and you will not find it.

---

## The plans

| | Free | Pro | Business | Enterprise |
|---|---|---|---|---|
| Price | Free | $12 / month | $49 / month | Custom |
| BIN & institution lookup | ✓ | ✓ | ✓ | ✓ |
| The full local database | ✓ | ✓ | ✓ | ✓ |
| Basic filters | ✓ | ✓ | ✓ | ✓ |
| Database updates | ✓ | ✓ | ✓ | ✓ |
| CSV / JSON / TXT export | 500 rows | 50,000 | Unlimited | Unlimited |
| Advanced search, saved searches, favourites | | ✓ | ✓ | ✓ |
| Watchlists & change alerts | | 5 | 50 | Unlimited |
| Institution Intelligence | | ✓ | ✓ | ✓ |
| Advanced analytics | | ✓ | ✓ | ✓ |
| PDF & XLSX reports, templates | | ✓ | ✓ | ✓ |
| Batch lookup | | | ✓ | ✓ |
| Database administration tools | | | ✓ | ✓ |
| Database edition | community | professional | business | enterprise |
| Devices | 1 | 3 | 10 | Unlimited |

**The free tier is a useful product, not a demo.** Unlimited offline lookups
against the complete database, with filters, exports and free updates. Nothing
is time-limited, nothing nags, and there are no countdowns or fake scarcity.

Packaging lives in `config/plans.json` as data. The shipped defaults in
`app/licensing/plans.py` apply when that file is missing or unreadable, and a
signed licence may override both.

---

## Building a gated surface

Use a `FeatureGate`. It holds the real content and an upgrade prompt, and shows
whichever the entitlement calls for:

```python
self.gate = FeatureGate(body, Feature.ADVANCED_ANALYTICS, self.surface)
self.gate.upgrade_requested.connect(lambda f: self.navigate(f"license:{f}"))
self.content.addWidget(self.gate, 1)

def refresh(self) -> None:
    self.gate.apply(self.context.entitlements.entitlement(Feature.ADVANCED_ANALYTICS))
```

The prompt names the feature, says what it does, says which plan includes it,
and offers one button. A locked page is never hidden and never broken — the
rest of the application stays fully usable behind it.

Navigation follows the same principle: paid entries stay in the sidebar with a
small `PRO` or `BUSINESS` badge rather than disappearing, so the product is
legible. The label elides before the badge does.

Limits are enforced where the work happens, not scattered through the UI:

```python
rows = self.context.search.export_rows(query)   # capped by Limit.EXPORT_ROWS
self.context.workspace.save_search(name, limit=entitlements.limit(Limit.SAVED_SEARCHES))
```

---

## Billing

The desktop application **does not process payments**. It has no card fields, no
payment SDK, no embedded merchant credentials, and stores nothing that could be
used to make a charge.

`app/services/billing_service.py` defines the interfaces a hosted billing
integration would implement — checkout URL, subscription state, portal link.
"Manage subscription" opens the account portal in the system browser. The
licensing service is the only thing that knows about entitlements, and it hands
the client a signed licence; how that licence was paid for is not the desktop
application's business.

---

## Database editions

Each plan maps to a database `edition` (community, professional, business,
enterprise). The manifest advertises the edition it contains and the licence
names the edition the user is entitled to, so a distribution server can serve
different packages to different plans through the same update pipeline. The
install path is identical either way.
