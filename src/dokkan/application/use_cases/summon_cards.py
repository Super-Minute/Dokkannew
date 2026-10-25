"""Summon use case: plain rounds or target-card hunting (memories 09, 02, 12).

Recovered command grammar (verbatim from the target's strings):

- ``summon <id> <m/s> [count]`` — m=multi, s=single; count is 1-1000 summon
  ROUNDS, not copies; ``999`` means "until stones run out".
- ``summon <id> <m/s> <IDxN[,IDxN...]>`` — seek each IDxN total INCLUDING
  owned copies (ex. ``summon 2196 m 1034010x2,1034020x1``).

This is an independent, single-account implementation: it talks only to
the game API on the user's own save and contains no licensing or tier
system (memory 13 records the recovered facts about the original's
licensing for spec completeness).

Semantics implemented here:

- With targets, ``fetch_account`` runs ONCE, before the first round, to
  subtract owned copies of each target id from the needed counts.
- Rounds draw one at a time (``gateway.summon(..., count=1)``) so the loop
  can stop the moment every IDxN total is reached — or keep the partial
  report when the game refuses (an ApplicationError such as out of stones).
- ``count == 999`` means "until the game refuses", capped at 1000 rounds;
  any other count is the exact round budget.
"""
from __future__ import annotations

from dataclasses import dataclass

from dokkan.application.errors import ApplicationError, InvalidRuns
from dokkan.application.ports import GameGateway

UNTIL_REFUSED = 999  # "summon ... 999" = repeat until stones run out (memory 09)
ROUND_CAP = 1000  # "count is 1-1000 summon rounds, not copies" (memory 09)


class InvalidTargets(ApplicationError):
    """A target-card list could not be parsed."""


def parse_targets(text: str) -> dict[int, int]:
    """Parse ``IDxN[,IDxN...]`` into ``{card_id: copies}``.

    ``xN`` may be omitted (N defaults to 1); whitespace is tolerated;
    repeated ids accumulate. Anything else raises :class:`InvalidTargets`
    (an empty or blank list is malformed: omit the argument instead).
    """
    if not isinstance(text, str) or not text.strip():
        raise InvalidTargets("target list is empty")
    targets: dict[int, int] = {}
    for item in text.split(","):
        item = "".join(item.split())  # tolerate whitespace around ids and 'x'
        if not item:
            raise InvalidTargets(f"empty target item in {text!r}")
        id_text, sep, count_text = item.partition("x")
        if not id_text.isdigit():
            raise InvalidTargets(f"target id is not a number: {item!r}")
        if sep and not count_text.isdigit():
            raise InvalidTargets(f"target count is not a number: {item!r}")
        count = int(count_text) if sep else 1
        if count < 1:
            raise InvalidTargets(f"target count must be >= 1: {item!r}")
        card_id = int(id_text)
        targets[card_id] = targets.get(card_id, 0) + count
    return targets


@dataclass(frozen=True)
class SummonReport:
    rounds: int
    drawn: int
    targets_met: bool
    progress: tuple[tuple[int, int], ...] = ()  # (card_id, still_needed)


@dataclass(frozen=True)
class SummonCards:
    gateway: GameGateway

    def execute(self, banner_id: int, multi: bool, count: int,
                targets: dict[int, int] | None = None) -> SummonReport:
        if count < 0:
            raise InvalidRuns(f"count must be >= 0, got {count}")
        needed = self._remaining(targets) if targets else None
        budget = ROUND_CAP if count == UNTIL_REFUSED else count
        rounds = drawn = 0
        while rounds < budget and (needed is None or any(needed.values())):
            try:
                cards = self.gateway.summon(banner_id, multi, 1)
            except ApplicationError:
                break  # e.g. out of stones: keep the partial report
            rounds += 1
            drawn += len(cards)
            if needed:
                for card in cards:
                    if needed.get(card.id, 0) > 0:
                        needed[card.id] -= 1
        if needed is None:
            return SummonReport(rounds, drawn, True, ())
        return SummonReport(rounds, drawn, not any(needed.values()),
                            tuple(sorted(needed.items())))

    def _remaining(self, targets: dict[int, int]) -> dict[int, int]:
        """Needed counts after subtracting owned copies ("including owned
        copies", memory 09); the account is fetched exactly once, here."""
        owned: dict[int, int] = {}
        for card in self.gateway.fetch_account().cards:
            owned[card.id] = owned.get(card.id, 0) + 1
        return {card_id: max(0, want - owned.get(card_id, 0))
                for card_id, want in targets.items()}
