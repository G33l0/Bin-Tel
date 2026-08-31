"""The lookup engine: range resolution, entity resolution and confidence.

This package holds the reasoning that turns a typed prefix into a defensible
statement about an institution. The services in :mod:`app.services` orchestrate
and cache; the repositories in :mod:`app.repositories` fetch; this is where the
decisions are made and justified.

Three rules run through all of it:

* **Specific beats broad.** An eight-digit assignment or an account range
  always outranks the six-digit root it happens to sit under.
* **Evidence, not arithmetic.** Two numerically adjacent BINs are not related
  because they are adjacent. Proximity is a weak analytical signal and is never
  sufficient on its own to name an institution.
* **Unknown beats wrong.** Where the evidence does not support a conclusion,
  the engine says so rather than picking the closest thing it found.
"""

from app.lookup.evidence import (
    EvidenceLevel,
    LookupConfidence,
    ResultConfidence,
    score_relationship,
)
from app.lookup.strategy import LookupStrategy, MatchSpecificity

__all__ = [
    "EvidenceLevel",
    "LookupConfidence",
    "LookupStrategy",
    "MatchSpecificity",
    "ResultConfidence",
    "score_relationship",
]
