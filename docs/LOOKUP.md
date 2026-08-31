# The lookup engine

Bin-Tel's job is to say which institution issued a card, and to be right. That
is harder than it looks, and most of the design here exists to avoid one
specific failure: confidently naming the wrong bank.

---

## The model

The application does **not** model `BIN → Bank`. It models a chain of
allocations:

```
IIN / BIN
   ↓
BIN range / account range
   ↓
Issuer / institution
   ↓
Institution relationships
   ↓
Product attributes · Geography · Network
```

An institution may hold many BINs, many eight-digit assignments, many ranges,
across many networks, products and countries, through subsidiaries, under
several names, and its holdings change over time. Every one of those is
one-to-many or many-to-many, and the schema says so.

---

## Six and eight digits are different allocations

A six-digit root and an eight-digit assignment beneath it are **not** the same
thing seen at different resolutions. In eight-digit issuance a root can be
shared between issuers, so:

```
410000        → Cascade Financial Group
  41000012    → Northshore Credit Union
  41000034    → Cascade Retail Bank
```

Answering a query for `41000012` from the `410000` record would attribute that
card to the wrong bank. So `bins` stores the prefix, its **assigned length**
and its **type** (`root`, `extended`, `range`), and the engine never collapses
one into the other.

Two consequences worth stating explicitly:

* Searching `410000` returns the root record — never one of its children, which
  may belong to somebody else. The interface says how many more specific
  assignments exist beneath it.
* Bin-Tel never generates the hundred eight-digit values under a root. An
  eight-digit value is an assignment only where an assignment is recorded.

---

## Specificity decides

Every allocation containing the query is gathered, then ranked. The order *is*
the precedence rule:

| Rank | Match | Meaning |
|---:|---|---|
| 6 | Exact 8-digit | An eight-digit assignment matching exactly |
| 5 | Account range | A network account range — the most specific authoritative allocation |
| 4 | Exact 6-digit | A six-digit assignment matching exactly |
| 3 | Broader range | A range wider than the query |
| 2 | 6-digit root | A shorter assignment whose span contains the query |
| 1 | Inferred | Reached through an institution record, not an allocation |

The narrowest allocation wins, and ties break on span. A broad prefix can never
override a specific assignment or an account range.

Losing candidates are not discarded — they come back with the result, so the
interface can say "a broader range also covers this".

---

## Numeric proximity is not evidence

`414720`, `414721` and `414722` are three allocations that happen to be
adjacent. They may belong to one bank; they may not. Nothing follows from
adjacency, and the engine never uses it to attach an institution. A prefix with
no record resolves to nothing, even when its neighbours are well known.

Proximity is available as a weak analytical signal. It is never sufficient on
its own.

---

## Luhn is not part of this

The Luhn check digit validates the *format* of a card number. It says nothing
about who issued it, which network runs it, or who owns the BIN. It is never
used as evidence for any of those, and it plays no part in the lookup path at
all — there is a test asserting the engine contains no reference to it.

---

## Confidence comes from evidence

A confidence figure is derived from *what kind of evidence supports the
answer*, graded on a fixed hierarchy, and it carries its reasons.

| Level | Evidence | Base |
|---:|---|---:|
| 1 | Exact authoritative issuer/range relationship | 0.97 |
| 2 | Validated network account-range relationship | 0.93 |
| 3 | Strong canonical institution relationship | 0.85 |
| 4 | Validated reference data | 0.76 |
| 5 | Multiple independent datasets agree | 0.70 |
| 6 | Name / address / entity-resolution evidence | 0.55 |
| 7 | Weak inference | 0.30 |

Then adjusted: independent agreement raises it, an associative relationship (a
parent, a processor) lowers it, a historical relationship lowers it, answering
from a broader allocation lowers it, and **the published record's own
confidence caps it** — the engine cannot be more certain than the data it is
reading.

Reported as `VERIFIED` · `HIGH` · `MEDIUM` · `LOW` · `CONFLICTED` · `UNKNOWN`.
Nothing is ever reported as 100%: this is reference data about a changing
world.

---

## Multiple relationships, and conflicts

A BIN can legitimately name several institutions — an issuer, a parent group,
a processor, a predecessor. **All of them are returned**, each with its type,
standing, effective period and its own confidence. The result card lists them;
nothing is hidden to produce a tidier answer.

Two *current issuing* claims naming different institutions is a different
thing: a disagreement. The conflict resolver tries to settle it on structural
grounds, in order —

1. specificity, 2. current over historical, 3. later effective date,
4. same institution under two names, 5. narrower allocation, 6. materially
better evidence

— and where none of those separates the two records, the result is
`CONFLICTED`, **both readings are shown**, and neither is deleted.

---

## Time

Relationships have lifetimes. `530001` was issued by Cascade from 2020 to 2024
and by Meridian since; both facts are stored, the current one answers by
default, and the former issuer is shown as historical with its period. A former
issuer is never promoted over a current one.

---

## Unknown beats wrong

Where the evidence does not support a conclusion, the engine says so:

* a prefix with no record → not found;
* a prefix present but naming no institution → found, unresolved, `UNKNOWN`;
* a name that only fuzzily matches → a *candidate*, never an applied
  relationship.

The goal is not to return a bank for every possible input. It is to return the
most accurate relationship the data supports, and to represent ambiguity
honestly.

---

## Institution → BIN

Asking "which BINs does this bank have?" follows every route into the
portfolio: the institution's own current relationships, its **historical**
ones, its **subsidiaries**, **predecessors** and **brands**, and its allocated
**ranges**. Results are deduplicated by BIN value — never by row id — and
grouped by network, country and prefix length.

A processor association deliberately does not inherit a portfolio: processing
a portfolio is not owning it.

---

## Entity resolution

Names are resolved through a graded ladder, and only the top four rungs are
applied automatically:

| Match | Established by | Applied? |
|---|---|---|
| `EXACT` | The legal name, exactly | yes |
| `CANONICAL` | The normalized name, in the same country | yes |
| `ALIAS` | A recorded alias | yes |
| `STRONG` | Names agree *and* independent identifiers corroborate | yes |
| `POSSIBLE` | Plausible, nothing corroborates it | no — offered |
| `CONFLICT` | Several equally good candidates | no — the caller decides |
| `UNKNOWN` | Nothing matched well enough | no |

Similar names are never merged on similarity alone. A merge needs a score above
the threshold **and** corroboration — a shared website host, a matching
SWIFT/BIC, shared BINs, a matching postal code.

One subtlety worth knowing: the country arriving with a record is the country
the **BIN** is issued in, not the institution's domicile. One bank issues in
several markets, so issuance country is used as *evidence* and never as an
identity discriminator — otherwise a single bank splits into one record per
market.

---

## Staging

Imported data never lands directly in production:

```
RAW → STAGING → NORMALIZE → VALIDATE → RESOLVE → conflict check → PROMOTE
```

A record that fails a step stops there and keeps the reason. A conflicted
resolution is held for review. A repair the pipeline had to make — transposed
range endpoints, say — is recorded rather than applied silently. A bad feed
spoils the staging table, not the database people are looking things up in.

See [DATABASE.md](DATABASE.md) for the schema and
[DEPLOYMENT.md](DEPLOYMENT.md) for the release pipeline that runs it.
