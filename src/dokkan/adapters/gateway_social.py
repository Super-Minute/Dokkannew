"""Social and battle operations: friends/supporters, missions beyond
accept, apologies, stage dropout, origin/z/kobetu battles, rankings
(memories 02, 12). ``SocialBattleMixin`` is composed onto
:class:`~dokkan.adapters.http_gateway.HttpGateway` and reuses its ``_call``
transport; it imports no siblings.

Recovered verbatim shapes (memory 02/12):
- friends/messages/members/supporters/support_leaders/missions/rankings are
  plain GETs; ``/members/`` keeps its trailing slash, ``/supporters`` takes
  an optional ``difficulty`` query param.
- ``/missions`` accepts an optional Rails-style array param
  ``mission_category_ids[]``; the shared ``_call`` transport urlencodes its
  params dict in a single pass, so this slice sends the id list as the
  str()'d list rather than true per-id repetition (documented-unconfirmed
  wire shape — a live capture would confirm doseq-style repetition instead).
- ``/missions/finish`` and ``/missions/put_forward`` POST a ``mission_id``.
- ``/apologies/accept`` and ``/stages/{id}/dropout`` are bare POSTs.
- origin battles: ``GET /origin_battles/briefing`` takes optional
  ``origin_battle_id``/``stage_id``; ``start``/``finish``/``retire`` are
  ``POST /origin_battles/{id}/{op}``.
- z battles: ``GET /z_battles/{id}/briefing`` takes arbitrary params
  (``difficulty``/``force_update``/``team_num``); ``start``/``finish`` are
  ``POST /z_battles/{id}/{op}``.
- kobetu (turn-based) battles: ``POST /kobetu_battles/{id}/commands/{cmd}``
  for the verbatim ``KOBETU_COMMANDS`` strings recovered from the binary's
  strings blob (``resume``/``transit_round`` are separate, non-``commands/``
  endpoints and are out of scope for this slice).
"""
from __future__ import annotations

# Verbatim kobetu-battle command strings (memory 02, strings extraction).
KOBETU_COMMANDS = (
    "execute_trigger_dokkan_mode",
    "execute_trigger_skill",
    "next_turn",
    "take_energy_ball",
    "use_group_change",
)


class SocialBattleMixin:
    # -- social ---------------------------------------------------------------
    def friends(self) -> dict:
        return self._call("GET", "/friendships")

    def messages(self) -> dict:
        return self._call("GET", "/messages")

    def members(self) -> dict:
        return self._call("GET", "/members/")

    def supporters(self, difficulty: int | None = None) -> dict:
        params = {"difficulty": str(difficulty)} if difficulty is not None else None
        return self._call("GET", "/supporters", params=params)

    def support_leaders(self) -> dict:
        return self._call("GET", "/support_leaders")

    # -- missions ---------------------------------------------------------------
    def list_missions(self, category_ids: list[int] | None = None) -> dict:
        params = None
        if category_ids:
            params = {"mission_category_ids[]": str(category_ids)}
        return self._call("GET", "/missions", params=params)

    def finish_mission(self, mission_id: int) -> dict:
        return self._call("POST", "/missions/finish", payload={"mission_id": mission_id})

    def put_forward_mission(self, mission_id: int) -> dict:
        return self._call("POST", "/missions/put_forward", payload={"mission_id": mission_id})

    # -- apologies -------------------------------------------------------------
    def accept_apology(self) -> dict:
        return self._call("POST", "/apologies/accept")

    # -- stage dropout -----------------------------------------------------------
    def dropout(self, stage_id: int) -> dict:
        return self._call("POST", f"/stages/{stage_id}/dropout")

    # -- origin battles ----------------------------------------------------------
    def origin_battle_briefing(self, origin_battle_id: int | None = None,
                                stage_id: int | None = None) -> dict:
        params = {}
        if origin_battle_id is not None:
            params["origin_battle_id"] = str(origin_battle_id)
        if stage_id is not None:
            params["stage_id"] = str(stage_id)
        return self._call("GET", "/origin_battles/briefing", params=params or None)

    def origin_battle(self, op: str, battle_id: int, payload: dict | None = None) -> dict:
        return self._call("POST", f"/origin_battles/{battle_id}/{op}", payload=payload)

    # -- z battles ----------------------------------------------------------------
    def z_battle(self, op: str, battle_id: int, params: dict | None = None,
                  payload: dict | None = None) -> dict:
        if op == "briefing":
            return self._call("GET", f"/z_battles/{battle_id}/briefing", params=params)
        return self._call("POST", f"/z_battles/{battle_id}/{op}", payload=payload)

    # -- kobetu battles --------------------------------------------------------------
    def kobetu_command(self, battle_id: int, command: str, payload: dict | None = None) -> dict:
        return self._call(
            "POST", f"/kobetu_battles/{battle_id}/commands/{command}", payload=payload)

    # -- rankings ----------------------------------------------------------------
    def rankings(self) -> dict:
        return self._call("GET", "/rankings")
