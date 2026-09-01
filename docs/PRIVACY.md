# Privacy

Bin-Tel is a personal research tool. It runs on your machine, against a
database on your machine, and it is designed so that using it produces no
record anywhere else.

---

## What Bin-Tel never handles

Regardless of settings or anything you type into it, Bin-Tel **never** stores,
collects, validates or processes:

- full payment card numbers (PANs)
- CVV / CVC codes
- PINs
- magnetic-stripe or chip track data
- cardholder names or account numbers
- passwords or authentication credentials

It is strictly a BIN/IIN and issuer-metadata tool. Lookup input is truncated to
a BIN-length prefix before it is used, and exports contain BIN metadata only.

---

## What leaves this machine

Three things, and only when you ask for them:

1. **A database update**, from the manifest URL configured in Settings →
   Updates. The request carries nothing but the manifest and package paths.
2. **An import from a remote data source you configured yourself**, when you
   run that import.
3. **A binlist.net lookup**, if you switch it on in Settings → Privacy and
   press the button. It sends one BIN — truncated to at most 8 digits, so a
   full card number cannot leave the machine — and nothing else. Off by
   default, never automatic, never in the background. See
   [SECOND_OPINION.md](SECOND_OPINION.md).

That is the whole list. There is no telemetry, no usage reporting, no crash
reporting, no account, no licence check and no analytics endpoint. Bin-Tel
does not phone home, and it works fully offline against the last database you
installed — including when an update check fails.

---

## What stays on this machine

| | |
|---|---|
| Your searches and search history | `bintel-user.sqlite`, local only |
| Saved searches, favourites, watchlists | `bintel-user.sqlite`, local only |
| Report templates and generated reports | your reports folder |
| Configuration | `config.json` in the config directory |
| Logs | the logs directory, rotated and pruned on the schedule you set |

Search history can be turned off entirely in Settings → Privacy. Everything
above lives in plain files you can inspect, back up, or delete.

---

## Logging

Logs record what the application did, never what your data was. The log
formatter redacts anything that looks like a long digit string, and never
writes a full lookup input. Deleting the logs directory is always safe.
