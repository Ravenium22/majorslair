"""Drawing raffle winners.

The draw happens on the server, from the operating system's secure random source, and every
draw is written to the audit trail with the whole pool and the winners. A draw can then be
shown to anyone who asks, and a redraw can never happen quietly: it is a second entry.
"""

from __future__ import annotations

import secrets
from random import Random


def draw_winners(pool: list[str], count: int, rng: Random | None = None) -> list[str]:
    """`count` different winners from `pool`, each equally likely, in the order drawn."""
    unique = list(dict.fromkeys(pool))
    if count < 1:
        raise ValueError("Pick at least one winner")
    if count > len(unique):
        raise ValueError(
            f"There are only {len(unique)} eligible entrant{'' if len(unique) == 1 else 's'} to draw from"
        )
    return (rng or secrets.SystemRandom()).sample(unique, count)
