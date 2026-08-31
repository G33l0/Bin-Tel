# Privacy

Bin-Tel is a local application. Your lookups run against a database on your own
machine, and nothing about them leaves it.

---

## What Bin-Tel never handles

Regardless of settings, plan, or anything you type into it, Bin-Tel **never**
stores, collects, validates, transmits or processes:

- full payment-card numbers (PANs)
- CVV / CVC / CVV2 codes
- PINs
- magnetic-stripe or chip track data
- cardholder names
- bank account numbers
- passwords or authentication credentials

Enter something card-length into any search field and it is **refused** — not
truncated, not searched, not logged. `validate_bin()` rejects anything longer
than 8 digits with a message explaining that Bin-Tel looks up issuer
identification numbers only.

This is enforced in three independent places: input validation refuses it, the
log redaction filter masks any 13–19 digit run that reaches a log line anyway,
and the telemetry sanitiser drops any 12+ digit numeric string and any
forbidden key outright.

---

## Where your data lives

Everything is on your machine. See [DATABASE.md](DATABASE.md) for the exact
paths.

| | |
|---|---|
| The intelligence database | Downloaded from the configured distribution server |
| Your saved searches, favourites, watchlists, history, templates | `bintel-user.sqlite`, local only |
| Your licence | `bintel-user.sqlite`, local only |
| Logs | Local, rotated, redacted |

Your search history is stored locally so the search box can offer it back to
you. It is never uploaded. It can be turned off, and cleared, in Settings →
Search.

---

## Network connections

Bin-Tel makes exactly three kinds of outbound request, all over HTTPS:

1. **The database manifest and package**, from the URL you configure. Nothing
   about your usage is sent — it is a plain fetch.
2. **Licence activation and revalidation**, if you activate a licence. This
   sends the licence key, the random device identifier and the device name you
   chose.
3. **Telemetry**, only if you turn it on.

That is all. There is no analytics SDK, no crash reporter phoning home, no ad
network, and no third-party script anywhere in the application.

---

## Telemetry

**Off by default.** Turning it on is an explicit choice in Settings → Privacy &
Telemetry, where the page lists exactly what would be sent and exactly what
never is.

### What it would collect

Aggregated, bucketed product events:

- application started / closed, startup duration, session length, active theme
- a database was installed, updated, verified, backed up or restored — with
  versions, durations and **bucketed** sizes
- which *named feature* was used, and which was blocked by plan
- report and export events: type, format, and a **bucketed** row count
- licence state changes: the plan, never the key
- error events: the exception type and the surface it happened on

Counts are bucketed (`1-10`, `11-100`, `101-1000`, …) rather than exact.

### What it never collects

BINs. IINs. Card numbers, CVVs, PINs, cardholder names. Search queries or search
terms of any kind. Institution names you looked at. Email addresses. File paths.
Licence keys. IP addresses collected by the application. Your database. Your
search history. Any free-form text you typed.

### How that is guaranteed

The vocabulary is a closed set. `app/telemetry/events.py` declares every event
and, per event, the exact keys it may carry. `sanitise()` drops:

- any key not on that event's allow-list
- any key in `FORBIDDEN_KEYS`, even if an allow-list were edited to permit it
- any value that is not a plain scalar
- any string longer than the length cap
- any numeric-looking string of 12 or more digits

A careless call site cannot leak a value, because the payload is rebuilt from
the allow-list rather than filtered.

### The queue

Events go to a local queue and are uploaded in batches. Turning telemetry off
**clears the queue**. Events older than 30 days are discarded rather than
retried forever, and the queue is capped so a permanently offline installation
cannot grow an unbounded file. A failed upload is invisible and harmless — the
events stay queued and the application behaves identically.

Counters keep counting locally even with telemetry off, because they also drive
the usage summary you can see on the Privacy page. With telemetry off there is
nothing to upload them with.

### The installation identifier

A random UUID4 generated on first run. Not derived from your hardware, your
hostname, your username or your network. You can reset it at any time, which
makes prior events unlinkable to the new one.

---

## Device identification

The device identifier used for licensing is `sha256(APP_ID + install_id)` — a
hash of a random value, salted with the application id so the same installation
cannot be correlated across unrelated products. There is deliberately **no**
hardware fingerprinting: no MAC address, no disk serial, no CPU id, no
canvas-style probing.

---

## Logs

Logs are local and rotated. Every record passes through `RedactionFilter`
before it is written, which masks card-length digit runs, `password=`,
`token=`, `secret=`, `api_key=`, `cvv=`, `pin=` and similar pairs, whole
`Authorization` values, and magnetic-stripe track payloads.

BINs (6–8 digits) survive redaction, because they are the subject of the
application and are not sensitive.

---

## Your controls

| Setting | Where |
|---|---|
| Turn telemetry on or off | Settings → Privacy & Telemetry |
| See exactly what is queued | Settings → Privacy & Telemetry |
| Clear the telemetry queue | Settings → Privacy & Telemetry |
| Reset the installation identifier | Settings → Privacy & Telemetry |
| Turn off search history | Settings → Search |
| Clear search history | Settings → Search |
| Deactivate the licence on this device | Plan & Licence |
| Delete everything | Delete the data and configuration directories |
