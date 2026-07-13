"""Source-by-volume tally (pure).

Turns the sampled work queue into a Pareto ranking so the highest-volume Sources
can be templated first (the Phase 8+ Template work is ordered by this). No I/O.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Iterable

from .models import MessageMeta
from .source import derive_source


@dataclass(frozen=True)
class SourceCount:
    source: str
    count: int
    pct: float             # share of total, 0..100
    cumulative_pct: float  # running total for the Pareto cut, 0..100


def tally_sources(messages: Iterable[MessageMeta]) -> list[SourceCount]:
    """Tally Emails by Source, ranked highest-volume first.

    Ties break alphabetically for a stable, testable ordering. Percentages and a
    running cumulative share make the Pareto point easy to read.
    """
    counts = Counter(derive_source(m.from_addr, m.subject) for m in messages)
    total = sum(counts.values())
    if total == 0:
        return []

    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    result: list[SourceCount] = []
    running = 0
    for source, count in ranked:
        running += count
        result.append(
            SourceCount(
                source=source,
                count=count,
                pct=round(100 * count / total, 1),
                cumulative_pct=round(100 * running / total, 1),
            )
        )
    return result
