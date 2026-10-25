"""Account and misc endpoints: user state, resources/home, teams, item
reverse resolutions, giru nav, iap rails, event campaigns (memories 02, 12).

Every wrapper here is a thin ``_call`` route over an endpoint recovered
verbatim from the target's strings (``/user``, ``/user/mydata``,
``/user_areas``, ``/items``, ``/system``, ``/status``, ``/config``,
``/resources/home``, ``/teams``, ``/team_save``,
``/item_reverse_resolutions/awakening_items``, ``/giru_nav/unacquired``,
``/iap_rails/itunes_products?platform_country=``, ``/events/eventkagi_events``,
``/eventkagi_items``, ``/cooperation_campaigns``, ``/joint_campaigns`` (+``/accept``),
``/missions/mission_board_campaigns``, ``/announcements/notify``). Response
shapes are unconfirmed, so the decrypted bodies are returned as raw dicts.

``MiscMixin`` is composed onto
:class:`~dokkan.adapters.http_gateway.HttpGateway` and reuses its ``_call``
transport; it imports no siblings.

Wire details the recovered spec leaves unconfirmed (documented choices):
- ``GET /user`` — memory 02 lists both GET and POST; GET is the read shape;
- ``POST /team_save`` carries the caller's team fields verbatim (memory 12
  lists ``team``, ``deck_num``, ``card_ids``, ``team_cards``, ``members``,
  ``sub_leader`` among them; whether more are required is unconfirmed), so
  no field is added, renamed or defaulted here;
- ``POST /joint_campaigns/accept`` is sent with no body — whether the server
  wants a campaign id is unconfirmed;
- ``POST /announcements/notify`` is sent with no body (payload unconfirmed);
- ``GET /iap_rails/itunes_products`` sends ``platform_country`` only when
  given (the verbatim pattern is ``?platform_country=``); the default
  country value is unconfirmed;
- ``/resources/home`` flag values are coerced by truthiness to the verbatim
  ``"true"``/``"false"`` strings (the same style as the daily mixin's
  verbatim flag dict); other value shapes are not expected on the wire.
"""
from __future__ import annotations


class MiscMixin:
    """User/account reads and misc campaign endpoints (memory 02/12)."""

    # -- user / account --------------------------------------------------------

    def user(self) -> dict:
        """GET /user — full user state (rank, stones, zeni, ACT stamina)."""
        return self._call("GET", "/user")

    def mydata(self) -> dict:
        """GET /user/mydata."""
        return self._call("GET", "/user/mydata")

    def user_areas(self) -> dict:
        """GET /user_areas — area clear progress."""
        return self._call("GET", "/user_areas")

    # -- misc reads ------------------------------------------------------------

    def items(self) -> dict:
        """GET /items."""
        return self._call("GET", "/items")

    def system(self) -> dict:
        """GET /system."""
        return self._call("GET", "/system")

    def status(self) -> dict:
        """GET /status."""
        return self._call("GET", "/status")

    def config(self) -> dict:
        """GET /config."""
        return self._call("GET", "/config")

    def resources_home(self, **flags) -> dict:
        """GET /resources/home with the given flags as query params.

        Each flag value is coerced to the verbatim ``"true"``/``"false"``
        string style the daily mixin's recovered flag combo uses; the
        original polls e.g. ``random_login_bonuses=true``.
        """
        params = {name: "true" if flag else "false"
                  for name, flag in flags.items()}
        return self._call("GET", "/resources/home", params=params)

    # -- teams -----------------------------------------------------------------

    def teams(self) -> dict:
        """GET /teams."""
        return self._call("GET", "/teams")

    def save_team(self, payload: dict) -> dict:
        """POST /team_save with the caller-supplied team fields verbatim
        (memory 12: ``team``, ``deck_num``, ``card_ids``, ``team_cards``,
        ``members``, ``sub_leader`` among them; team building is
        client-side)."""
        return self._call("POST", "/team_save", payload=payload)

    # -- item reverse resolutions / giru nav -----------------------------------

    def item_reverse_resolutions(self) -> dict:
        """GET /item_reverse_resolutions/awakening_items."""
        return self._call("GET", "/item_reverse_resolutions/awakening_items")

    def giru_nav_unacquired(self) -> dict:
        """GET /giru_nav/unacquired."""
        return self._call("GET", "/giru_nav/unacquired")

    # -- iap rails -------------------------------------------------------------

    def itunes_products(self, platform_country: str | None = None) -> dict:
        """GET /iap_rails/itunes_products, with ``platform_country`` as a
        query param only when given (verbatim ``?platform_country=``
        pattern)."""
        params = ({"platform_country": platform_country}
                  if platform_country is not None else None)
        return self._call("GET", "/iap_rails/itunes_products", params=params)

    # -- event campaigns --------------------------------------------------------

    def eventkagi_events(self) -> dict:
        """GET /events/eventkagi_events."""
        return self._call("GET", "/events/eventkagi_events")

    def eventkagi_items(self) -> dict:
        """GET /eventkagi_items."""
        return self._call("GET", "/eventkagi_items")

    def cooperation_campaigns(self) -> dict:
        """GET /cooperation_campaigns."""
        return self._call("GET", "/cooperation_campaigns")

    def joint_campaigns(self, accept: bool = False) -> dict:
        """GET /joint_campaigns, or POST /joint_campaigns/accept when
        ``accept`` is true (memory 02 lists the ``/accept`` companion)."""
        if accept:
            return self._call("POST", "/joint_campaigns/accept")
        return self._call("GET", "/joint_campaigns")

    def mission_board_campaigns(self) -> dict:
        """GET /missions/mission_board_campaigns."""
        return self._call("GET", "/missions/mission_board_campaigns")

    # -- announcements ----------------------------------------------------------

    def announce(self) -> dict:
        """POST /announcements/notify."""
        return self._call("POST", "/announcements/notify")
