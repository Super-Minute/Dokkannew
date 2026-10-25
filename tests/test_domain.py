import dataclasses
import unittest

from dokkan.domain.errors import InsufficientStamina, InvalidTeam, InvalidValue
from dokkan.domain.models import (
    Account, Card, CardType, Difficulty, Rarity, Stage, Stamina, Team,
)
from dokkan.domain.policies import build_team, cards_to_sell, eligible_for_stage


def card(i: int, rarity: Rarity = Rarity.SR, type_: CardType = CardType.AGL, **kw) -> Card:
    return Card(id=i, name=f"c{i}", rarity=rarity, type=type_, **kw)


def stage(**kw) -> Stage:
    return Stage(**{"id": "s", "name": "x", "difficulty": Difficulty.EASY, "stamina_cost": 5, **kw})


class StaminaTests(unittest.TestCase):
    def test_spend(self):
        self.assertEqual(Stamina(10, 50).after_spending(4).current, 6)

    def test_boundary_is_affordable(self):
        self.assertTrue(Stamina(4, 50).can_afford(4))
        self.assertFalse(Stamina(3, 50).can_afford(4))

    def test_overspend_raises(self):
        with self.assertRaises(InsufficientStamina):
            Stamina(3, 50).after_spending(4)

    def test_negative_values_rejected(self):
        with self.assertRaises(InvalidValue):
            Stamina(-1, 50)
        with self.assertRaises(InvalidValue):
            Stamina(5, 50).after_spending(-5)


class ValidationTests(unittest.TestCase):
    def test_invalid_models_rejected(self):
        bad = {
            "empty name": lambda: Card(1, "", Rarity.N, CardType.AGL),
            "level 0": lambda: card(1, level=0),
            "negative attack": lambda: card(1, attack=-1),
            "negative cost": lambda: stage(stamina_cost=-1),
        }
        for label, build in bad.items():
            with self.subTest(label), self.assertRaises(InvalidValue):
                build()

    def test_models_are_immutable(self):
        with self.assertRaises(dataclasses.FrozenInstanceError):
            card(1).level = 2  # type: ignore[misc]


class TeamTests(unittest.TestCase):
    def test_rejects_empty_oversize_duplicates(self):
        for ids in ((), (1, 2, 3, 4, 5, 6, 7), (1, 1)):
            with self.subTest(ids=ids), self.assertRaises(InvalidTeam):
                Team(ids)


class AccountTests(unittest.TestCase):
    def test_box_is_full(self):
        cards = (card(1), card(2))
        st = Stamina(1, 1)
        self.assertTrue(Account("a", st, cards, card_capacity=2).box_is_full)
        self.assertFalse(Account("a", st, cards, card_capacity=3).box_is_full)
        self.assertFalse(Account("a", st, cards).box_is_full)


class PolicyTests(unittest.TestCase):
    def test_sell_skips_locked_favorite_and_high_rarity(self):
        cards = [
            card(1, Rarity.N), card(2, Rarity.R, locked=True), card(3, Rarity.SR, favorite=True),
            card(4, Rarity.SSR), card(5, Rarity.UR), card(6, Rarity.LR), card(7, Rarity.SR),
        ]
        self.assertEqual([c.id for c in cards_to_sell(cards)], [1, 7])

    def test_max_rarity_restriction(self):
        st = stage(max_rarity=Rarity.SR)
        self.assertTrue(eligible_for_stage(card(1, Rarity.SR), st))
        self.assertFalse(eligible_for_stage(card(2, Rarity.UR), st))

    def test_build_team_respects_type_and_orders_by_attack(self):
        st = stage(allowed_types=frozenset({CardType.TEQ}))
        cards = [
            card(1, type_=CardType.TEQ, attack=10),
            card(2, type_=CardType.AGL, attack=99),
            card(3, type_=CardType.TEQ, attack=50),
        ]
        self.assertEqual(build_team(cards, st).card_ids, (3, 1))

    def test_build_team_truncates_and_breaks_ties_by_lowest_id(self):
        cards = [card(i, attack=5) for i in (9, 3, 7)]
        self.assertEqual(build_team(cards, stage(), size=2).card_ids, (3, 7))
        self.assertEqual(len(build_team([card(i) for i in range(20)], stage()).card_ids), 6)

    def test_build_team_does_not_mutate_input(self):
        cards = [card(2, attack=1), card(1, attack=9)]
        build_team(cards, stage())
        self.assertEqual([c.id for c in cards], [2, 1])

    def test_build_team_rejects_bad_size_and_empty_pool(self):
        for size in (0, 7):
            with self.subTest(size=size), self.assertRaises(InvalidTeam):
                build_team([card(1)], stage(), size=size)
        with self.assertRaises(InvalidTeam):
            build_team([card(1)], stage(allowed_types=frozenset({CardType.INT})))


if __name__ == "__main__":
    unittest.main()
