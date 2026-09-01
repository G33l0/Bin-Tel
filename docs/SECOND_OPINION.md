# Second opinions — binlist.net

Bin-Tel can check a single BIN against [binlist.net](https://binlist.net), a
public lookup service, and show you what they say beside what you hold.

It is **off by default**. Turn it on in **Settings → Privacy → Second
opinions**, and a *Check binlist.net* button appears on the BIN Lookup page.

---

## What it is for

One thing: **finding out that you and somebody else disagree.**

The useful moments are narrow and worth naming:

* You look up a BIN that is **not in your list**, and want a starting point.
  The reading comes back as a row you can paste into `data/bin-list.csv`.
* You look up a BIN that **is** in your list, and binlist.net says something
  different. Every disagreement is listed — network, card type, country,
  issuer — and neither reading is discarded. That is a prompt to go and check,
  not a correction.

---

## What it is not for

**It cannot populate your database, and there is deliberately no way to try.**

binlist.net allows **five lookups an hour**. Filling a hundred BINs would take
twenty hours of continuous polling; a thousand would take a week. So there is
no bulk mode, no "enrich my whole list" button, and no background fetching.
The provider class exposes a single-BIN `lookup` and nothing else.

Their free tier covers **6-digit BINs**; 8-digit lookups are a paid feature. An
8-digit query is sent as-is and whatever comes back is reported for what it is.

---

## Why a reading is never saved automatically

Two reasons, and the second is the important one.

**Your list is the single source of truth.** The whole design rests on the
database being built from `data/bin-list.csv` and nothing else. If an external
service could write into it, that guarantee would be gone and your list would
start drifting from your database.

**Their data is partly built the way this application refuses to.** Their own
page says:

> "Some data is formed based on **assumptions we make by looking at adjoining
> cards**."

That is exactly the adjacency inference Bin-Tel rejects — `414720` and `414721`
being one apart says nothing about whether they share an issuer. They are
candid about it, adding "don't expect it to be perfect". So a reading is
presented as *somebody else's opinion, to be checked*, and it enters your data
only when you paste it in yourself.

---

## Licensing

binlist.net publishes **no terms of use**, and its companion data repository
`github.com/binlist/data` carries **no licence file** — no `LICENSE`,
`LICENSE.md`, `LICENSE.txt` or `COPYING`.

Bin-Tel therefore records the provider as **`REVIEW_REQUIRED`**: consulting one
BIN at a time is fine, bulk-copying or redistributing is not established as
permitted and is not attempted. Their repository is, by their own account,
roughly 2% of the data behind the service.

---

## What is sent, and what is not

**Sent:** the BIN, and nothing else. A query is reduced to digits and
**truncated to at most 8** before the request is built, so a full card number
pasted into the search box cannot leave the machine — this is enforced in the
provider and covered by a test.

**Not sent:** anything about you, your list, your database or your searches.
The request carries a user-agent and the documented `Accept-Version: 3` header.

**Never in the background.** A request happens when you press the button, and
at no other time.

---

## The allowance

Bin-Tel keeps to the published five-an-hour limit rather than discovering it by
being refused:

* The button shows how many you have left, and disables itself at zero.
* The sixth request in an hour is **refused locally** and never sent.
* The allowance is written to disk (`binlist-budget.json` in your config
  folder), so closing the application does not hand you a fresh five.
* If binlist.net returns `429` anyway, their accounting wins: the local
  allowance is emptied for the window they name in `Retry-After`.

---

## From the command line

```bash
python -m app.cli binlist 45717360
```

It refuses to run while the setting is off; pass `--force` for a one-off. The
output ends with a row ready to paste into your list:

```
bin,bank,network,card_type,brand,country,currency
45717360,Jyske Bank A/S,visa,debit,Visa Classic/Dankort,DK,DKK
```

Paste it into `data/bin-list.csv`, then `python -m app.cli rebuild`.

---

## Turning it off

Untick it in Settings → Privacy. The button disappears and nothing further is
sent. With it off, Bin-Tel makes no network calls at all except a database
update you ask for — see [PRIVACY.md](PRIVACY.md).
