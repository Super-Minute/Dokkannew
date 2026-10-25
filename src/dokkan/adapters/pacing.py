"""Pacing between game API calls (memory 10: bot_config ``client_delays``).

The original bot waits a randomized 20–21 s (per client) between its game
API calls to respect the service's rate limits. The cleanroom bot honors the
same configured pacing: every :class:`~dokkan.application.ports.GameGateway`
call made through :class:`PacedGateway` sleeps one randomized window before
running, except the first. The ``ping`` connectivity probe is deliberately
unpaced — it makes no authenticated game call.
"""
from __future__ import annotations

import random
import time

from dokkan.application.settings import DelayRange


class PacedGateway:
    """Wraps a GameGateway with the configured client delay between calls."""

    def __init__(self, inner, delay: DelayRange,
                 sleep=time.sleep, rng=random.uniform) -> None:
        self.inner = inner
        self.delay = delay
        self._sleep = sleep
        self._rng = rng
        self._calls = 0

    def _throttle(self) -> None:
        if self._calls:
            self._sleep(self._rng(self.delay.min, self.delay.max))
        self._calls += 1

    def ping(self):
        """Connectivity probe — delegated unpaced (no game call involved)."""
        ping = getattr(self.inner, "ping", None)
        return ping() if ping is not None else None

    def fetch_account(self):
        self._throttle()
        return self.inner.fetch_account()

    def list_events(self):
        self._throttle()
        return self.inner.list_events()

    def run_stage(self, stage, team):
        self._throttle()
        return self.inner.run_stage(stage, team)

    def sell_cards(self, card_ids):
        self._throttle()
        return self.inner.sell_cards(card_ids)

    def refill_stamina(self, source):
        self._throttle()
        return self.inner.refill_stamina(source)

    def list_banners(self):
        self._throttle()
        return self.inner.list_banners()

    def summon(self, banner_id, multi, count=1):
        self._throttle()
        return self.inner.summon(banner_id, multi, count)

    def accept_gifts(self):
        self._throttle()
        return self.inner.accept_gifts()

    def accept_missions(self):
        self._throttle()
        return self.inner.accept_missions()

    def accept_login_bonus(self):
        self._throttle()
        return self.inner.accept_login_bonus()

    def run_board(self, board_id):
        self._throttle()
        return self.inner.run_board(board_id)

    def link_status(self):
        self._throttle()
        return self.inner.link_status()

    def __getattr__(self, name):
        """Forward anything not explicitly wrapped, with the same pacing.

        The inner gateway keeps gaining feature mixins; this keeps every
        game call paced without enumerating the methods here. It only
        fires for attributes normal lookup does not find, so the explicit
        methods above are unaffected (no double throttling).
        """
        if name.startswith("_"):
            raise AttributeError(name)
        inner = getattr(self.inner, name)
        if not callable(inner):
            return inner

        def paced(*args, **kwargs):
            self._throttle()
            return inner(*args, **kwargs)

        return paced
