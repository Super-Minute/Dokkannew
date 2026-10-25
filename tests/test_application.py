import unittest

from dokkan.application.ports import RefillSource
from dokkan.application.errors import InvalidRuns, NoTeamAvailable
from dokkan.application.settings import Settings, StaminaSettings
from dokkan.application.use_cases.farm_stage import FarmStage, StopReason
from dokkan.application.use_cases.list_events import ListEvents
from dokkan.application.use_cases.sell_cards import SellCards
from dokkan.domain.errors import InvalidTeam
from dokkan.domain.models import Card, CardType, Difficulty, Rarity, Stage, Stamina, Team
from fakes import FakeGateway

STAGE = Stage("1", "s", Difficulty.EASY, stamina_cost=10)


def cards(n, rarity=Rarity.SR):
    return [Card(i, f"c{i}", rarity, CardType.AGL, attack=i) for i in range(1, n + 1)]


class SellCardsTests(unittest.TestCase):
    def test_sells_only_sellable(self):
        gw = FakeGateway(cards=cards(2) + [Card(9, "ur", Rarity.UR, CardType.INT)])
        self.assertEqual(SellCards(gw).execute(gw.fetch_account()), [1, 2])
        self.assertEqual(gw.sold, [1, 2])

    def test_nothing_to_sell_makes_no_call(self):
        gw = FakeGateway(cards=[Card(9, "ur", Rarity.UR, CardType.INT)])
        self.assertEqual(SellCards(gw).execute(gw.fetch_account()), [])
        self.assertEqual(gw.sold, [])


class ListEventsTests(unittest.TestCase):
    def test_lists(self):
        self.assertEqual(ListEvents(FakeGateway()).execute()[0].name, "e")


class FarmStageTests(unittest.TestCase):
    def test_runs_requested_count_with_built_team(self):
        gw = FakeGateway(stamina=100, cards=cards(8))
        report = FarmStage(gw, Settings()).execute(STAGE, runs=3)
        self.assertEqual((report.runs, report.cleared, report.stopped_reason), (3, 3, StopReason.COMPLETED))
        self.assertEqual(gw.runs[0][1].card_ids, (8, 7, 6, 5, 4, 3))

    def test_explicit_team_skips_building(self):
        gw = FakeGateway(stamina=100)
        FarmStage(gw, Settings(team_build=False)).execute(STAGE, 1, team=Team((1,)))
        self.assertEqual(gw.runs[0][1].card_ids, (1,))

    def test_no_team_and_building_disabled_fails(self):
        with self.assertRaises(NoTeamAvailable):
            FarmStage(FakeGateway(), Settings(team_build=False)).execute(STAGE, 1)

    def test_unbuildable_team_propagates(self):
        with self.assertRaises(InvalidTeam):
            FarmStage(FakeGateway(cards=[]), Settings()).execute(STAGE, 1)

    def test_meat_before_stone_by_default(self):
        gw = FakeGateway(stamina=0, cards=cards(1), refills={s: 50 for s in RefillSource})
        FarmStage(gw, Settings()).execute(STAGE, 1)
        self.assertEqual(gw.refill_calls, [RefillSource.MEAT])

    def test_stone_first_when_meat_not_preferred(self):
        gw = FakeGateway(stamina=0, cards=cards(1), refills={s: 50 for s in RefillSource})
        FarmStage(gw, Settings(StaminaSettings(prefer_meat=False))).execute(STAGE, 1)
        self.assertEqual(gw.refill_calls, [RefillSource.STONE])

    def test_falls_back_when_first_source_unavailable(self):
        gw = FakeGateway(stamina=0, cards=cards(1), refills={RefillSource.STONE: 50})
        self.assertEqual(FarmStage(gw, Settings()).execute(STAGE, 1).runs, 1)
        self.assertEqual(gw.refill_calls, [RefillSource.MEAT, RefillSource.STONE])

    def test_stone_disabled_stops_when_dry(self):
        gw = FakeGateway(stamina=0, cards=cards(1), refills={RefillSource.STONE: 50})
        report = FarmStage(gw, Settings(StaminaSettings(use_stone=False))).execute(STAGE, 1)
        self.assertEqual((report.runs, report.stopped_reason), (0, StopReason.OUT_OF_STAMINA))
        self.assertNotIn(RefillSource.STONE, gw.refill_calls)

    def test_partial_refill_repeats_same_source_before_next(self):
        class Partial(FakeGateway):
            def refill_stamina(self, source):
                self.refills[source] = 6
                return super().refill_stamina(source)

        gw = Partial(stamina=0, cards=cards(1))
        FarmStage(gw, Settings()).execute(STAGE, 1)  # cost 10, each meat gives 6
        self.assertEqual(gw.refill_calls, [RefillSource.MEAT, RefillSource.MEAT])

    def test_refill_that_restores_nothing_does_not_loop(self):
        class Dud(FakeGateway):
            def refill_stamina(self, source):
                self.refill_calls.append(source)
                return Stamina(self.stamina, self.maximum)

        gw = Dud(stamina=0, cards=cards(1))
        self.assertEqual(FarmStage(gw, Settings()).execute(STAGE, 1).stopped_reason, StopReason.OUT_OF_STAMINA)
        self.assertEqual(len(gw.refill_calls), 2)  # once per source

    def test_stops_mid_run_when_stamina_runs_out(self):
        gw = FakeGateway(stamina=25, cards=cards(1))
        report = FarmStage(gw, Settings()).execute(STAGE, 5)
        self.assertEqual((report.runs, report.stopped_reason), (2, StopReason.OUT_OF_STAMINA))

    def test_no_stamina_spent_when_team_cannot_be_built(self):
        gw = FakeGateway(stamina=0, cards=[], refills={s: 50 for s in RefillSource})
        with self.assertRaises(InvalidTeam):
            FarmStage(gw, Settings()).execute(STAGE, 1)
        self.assertEqual(gw.refill_calls, [])

    def test_failed_clear_stops(self):
        gw = FakeGateway(stamina=100, cards=cards(1), clears=False)
        report = FarmStage(gw, Settings()).execute(STAGE, 5)
        self.assertEqual((report.runs, report.cleared, report.stopped_reason), (1, 0, StopReason.STAGE_FAILED))

    def test_zero_runs_and_negative_runs(self):
        gw = FakeGateway(cards=cards(1))
        self.assertEqual(FarmStage(gw, Settings()).execute(STAGE, 0).runs, 0)
        with self.assertRaises(InvalidRuns):
            FarmStage(gw, Settings()).execute(STAGE, -1)

    def test_auto_sell_when_box_full_keeps_team_cards(self):
        gw = FakeGateway(stamina=100, cards=cards(3), capacity=3)
        report = FarmStage(gw, Settings(auto_sell=True)).execute(STAGE, 1, team=Team((1,)))
        self.assertEqual((report.cards_sold, gw.sold), (2, [2, 3]))
        self.assertIn(1, [c.id for c in gw.cards])

    def test_auto_sell_disabled(self):
        gw = FakeGateway(stamina=100, cards=cards(2), capacity=2)
        report = FarmStage(gw, Settings(auto_sell=False)).execute(STAGE, 1, team=Team((1,)))
        self.assertEqual((report.cards_sold, gw.sold), (0, []))


if __name__ == "__main__":
    unittest.main()
