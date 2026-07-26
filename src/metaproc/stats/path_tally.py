"""Hierarchical additive prefix-tally helper.

A single contribution to a multi-segment path accrues to every prefix, so
taxonomy roll-ups like `waiting`, `waiting > throttling`, and
`waiting > throttling > rate_limits` fall out of one `add()` call.

Multiple parallel taxonomy *families* (time, cost, provider, model, tool,
resource, policy, plus arbitrary plugin-owned families) live side-by-side
on one `PathTally` instance — the same event can contribute to several
families at once without bookkeeping in the caller.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import cast

CANONICAL_SEPARATOR = " > "


def parse_canonical(canonical: str) -> tuple[str, ...]:
    """Tokenize ``"a > b > c"`` into ``("a", "b", "c")``.

    Whitespace around each segment is stripped; empty segments are dropped
    so the canonical form is robust to trailing separators or stray spaces.
    """
    return tuple(seg.strip() for seg in canonical.split(">") if seg.strip())


def render_canonical(path: tuple[str, ...]) -> str:
    """Render a structured path as canonical ``"a > b > c"`` text."""
    return CANONICAL_SEPARATOR.join(path)


class PathTally[T: (int, float)]:
    """Additive aggregator over hierarchical paths across multiple families.

    Contributions to path ``(a, b, c)`` accrue to every prefix:
    ``(a,)``, ``(a, b)``, and ``(a, b, c)``. Add a value once and read
    it back at any prefix.

    Type parameter ``T`` is the additive value type — typically ``int``
    (counts, tokens, bytes) or ``float`` (seconds, dollars).
    """

    def __init__(self) -> None:
        self._totals: dict[str, dict[tuple[str, ...], T]] = {}

    def add(self, family: str, path: tuple[str, ...], value: T) -> None:
        """Add ``value`` to ``path`` and every prefix of ``path`` in ``family``."""
        if not path:
            return
        family_totals = self._totals.setdefault(family, {})
        for i in range(1, len(path) + 1):
            prefix = path[:i]
            family_totals[prefix] = family_totals.get(prefix, type(value)()) + value  # type: ignore[operator]

    def add_from_text(self, family: str, canonical: str, value: T) -> None:
        """Add via canonical ``"a > b > c"`` text. Equivalent to :meth:`add`."""
        self.add(family, parse_canonical(canonical), value)

    def get(self, family: str, path: tuple[str, ...]) -> T:
        """Return the rolled-up value at ``path`` (includes deeper contributions).

        Returns the type's additive identity (0) when the family or path is
        absent, so callers can blindly read any prefix.
        """

        family_totals = self._totals.get(family)
        if family_totals is None:
            return cast(T, 0)
        if path in family_totals:
            return family_totals[path]
        return cast(T, 0)

    def iter_prefixes(self, family: str) -> Iterator[tuple[tuple[str, ...], T]]:
        """Yield ``(prefix, value)`` for every prefix recorded in ``family``."""
        family_totals = self._totals.get(family)
        if family_totals is None:
            return
        for prefix in sorted(family_totals.keys()):
            yield prefix, family_totals[prefix]

    def families(self) -> list[str]:
        """Return the list of families that have at least one contribution."""
        return sorted(self._totals.keys())

    def render_canonical(self, path: tuple[str, ...]) -> str:
        return render_canonical(path)
