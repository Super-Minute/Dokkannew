"""Greatest Warrior board (sugoroku map) operations (memories 02, 09, 12).

Recovered from the strings blob (2026-10-18): the board calls are
``POST /sugoroku_maps/{id}/start`` and ``POST /sugoroku_maps/{id}/finish``;
the original's auto flow drives boards 1-N via ``auto_complete_board1..7``.
Only these two endpoints are recovered — any intermediate board protocol
(``round``/``step``/``event_type`` locals sit near the start call, plus
``unlocked_square_statuses``) is documented-unconfirmed and needs a live
capture, as is the start payload's supporter selection
(``friend_id``/``support_user_card_id``/``is_cpu_supporter``/
``support_memory_id``). This slice therefore does start → finish with the
identity-only payload the transport seals by default.

The original tiers board auto-completion behind its license server; this
independent implementation contains no licensing system (memory 13).
"""
from __future__ import annotations

from dokkan.application.ports import StageResult


class BoardsMixin:
    """POST /sugoroku_maps/{id}/start + /finish (memory 12)."""

    def run_board(self, board_id: int) -> StageResult:
        self._call("POST", f"/sugoroku_maps/{board_id}/start")
        finished = self._call("POST", f"/sugoroku_maps/{board_id}/finish")
        # is_cleared is the battle-state field recovered verbatim (memory 12).
        if "is_cleared" in finished:
            return StageResult(cleared=bool(finished["is_cleared"]))
        return StageResult(cleared=bool(finished.get("success")))
