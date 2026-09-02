# Learning

Bin-Tel can improve its own database — from evidence it already holds, and from
sources outside this machine. Nothing it learns is written because a source
said so.

Everything learned becomes a **proposal**: a row naming its source, that
source's licence standing, the value currently held, the value offered, and the
evidence for it. A proposal changes no answer and appears in no lookup. It
waits.

```bash
python -m app.cli learn --local-only   # gather; writes nothing
python -m app.cli learned              # read what is waiting
python -m app.cli approve 3            # write one, with provenance
python -m app.cli reject 1 2           # decline, so they are not raised again
```

In the app: **Settings → Privacy → Learning**, and the review list under
**Database**.

---

## Two permissions, and neither implies the other

**The source must be authorized.** A source code you added to your settings.
Nothing adds itself, there is no default-on source, and an unauthorized source
is not consulted at all — it is not that its answers are ignored, it is that no
request is made.

**The fact must be approved.** Per fact, or in bulk once you have read them. A
source you trust enough to *ask* is not automatically one you trust to
*overwrite* what you curated by hand.

The one shortcut is deliberately narrow. **Apply what fills a blank** applies a
proposal only when all three hold:

- it fills a gap rather than contradicting something held;
- the source's licence is settled (`verified`, never `review_required`);
- you turned the setting on.

Anything that contradicts a value already in your database waits for you,
whatever the source, whatever its licence, and however confident it is. That is
the case where being wrong costs something.

---

## Where proposals come from

### Evidence already held

Needs no network and consults nothing, so it is on by default and runs after
each rebuild.

| Rule | What it proposes |
|---|---|
| `local:conflict` | A field where two sources disagreed and the build had to pick one. The other side is offered, so the choice can be revisited deliberately. |
| `local:institution` | A BIN with no country of its own, whose institution's country is known. Offered rather than applied, because where a bank *is* is not necessarily where a BIN is *issued*. |

### A source outside this machine

Only providers you authorize by name, only for BINs you name, and within their
published allowance. `binlist.net` is the one that ships. Its terms are not
settled — it publishes none, and its data repository carries no licence — so it
is marked `review_required`, which means **nothing from it can ever apply
automatically**, whatever your settings say.

```bash
python -m app.cli learn 414720 530001
```

Bin-Tel does not scrape. It does not read a site that has not published an
interface for it, bypass a rate limit, paywall or access restriction, or use an
undocumented endpoint. A source whose terms do not establish redistribution
stays marked as such on every fact it produced, and that marking travels into
the database with the value.

---

## What a learned fact may write

A whitelist, not "whatever was on the row":

| Subject | Writeable |
|---|---|
| BIN | `network`, `brand`, `card_level`, `card_type`, `funding_type`, `currency_code`, `country` |
| Institution | `website`, `legal_name`, `short_name`, `swift_bic`, `country` |

Absent on purpose: the prefix, its length, its type, and its numeric span.
Those are what a lookup **matches on**. Letting a learned fact edit them would
let a source silently *move* a BIN rather than describe one.

## Provenance

Applying writes two records beside the value — a normalization event naming the
rule (`learned:binlist.net`) and a claim naming the source and its licence. A
value learned this way can always be traced back to what proposed it, when, and
under what terms.

```bash
python -m app.cli origin 414720   # the source rows behind a BIN, verbatim
```

---

## Nothing a source said is reduced away

The curated columns are an *interpretation* of a source row, and a narrow one:
a country spelled three ways becomes one code, a coordinate pair Bin-Tel will
not assert as a bank's address is not stored as one, and a column it has no
field for has nowhere to go.

Narrower must not mean lossy. Every row is archived under its **own** headers,
in the file's own order, with the file name and line it came from. Nothing a
source said is discarded — including the values the reader refused, which is
what lets a refusal be checked rather than trusted.

It is never shown as a section of a lookup result. Results state what Bin-Tel
holds; `origin` shows the working behind it.
