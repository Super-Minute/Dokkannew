"""Renders a card collection as a standalone HTML page (no network, no JS
beyond a client-side sort) — the "box, as a webpage" view.

This is a read-only report built from a collection file already on disk; it
never talks to the game or to any site, so it fits the same offline rules as
the rest of ``collection_tools``.
"""
from __future__ import annotations

import html
from typing import Sequence

from dokkan.application.use_cases.collection_tools import plan_sale, plan_team, summarize_box
from dokkan.domain.errors import InvalidTeam
from dokkan.domain.models import Card

_STYLE = """
body { font: 14px/1.4 system-ui, sans-serif; margin: 2rem; color: #222; }
h1, h2 { margin-top: 2rem; }
table { border-collapse: collapse; width: 100%; margin-top: 0.5rem; }
th, td { text-align: left; padding: 0.35rem 0.6rem; border-bottom: 1px solid #ddd; }
th { cursor: pointer; user-select: none; background: #f4f4f4; position: sticky; top: 0; }
th:hover { background: #e9e9e9; }
tr.sellable { background: #fff6e5; }
tr.team { background: #e9f7ef; }
.summary { display: flex; gap: 2rem; flex-wrap: wrap; }
.summary div { background: #f4f4f4; border-radius: 8px; padding: 0.75rem 1rem; }
.rarity-LR, .rarity-UR { color: #b8860b; font-weight: 600; }
.note { color: #666; font-size: 0.9rem; }
"""

_SORT_SCRIPT = """
function sortTable(table, col, numeric) {
  const tbody = table.tBodies[0];
  const rows = Array.from(tbody.rows);
  const dir = table.dataset.sortCol == col && table.dataset.sortDir == "asc" ? "desc" : "asc";
  rows.sort((a, b) => {
    let x = a.cells[col].dataset.sort, y = b.cells[col].dataset.sort;
    if (numeric) { x = Number(x); y = Number(y); }
    return (x < y ? -1 : x > y ? 1 : 0) * (dir == "asc" ? 1 : -1);
  });
  rows.forEach(r => tbody.appendChild(r));
  table.dataset.sortCol = col;
  table.dataset.sortDir = dir;
}
"""


def _card_row(card: Card, *, sellable: bool, on_team: bool) -> str:
    classes = " ".join(c for c, on in (("sellable", sellable), ("team", on_team)) if on)
    name = html.escape(card.name)
    return (
        f'<tr class="{classes}">'
        f'<td data-sort="{card.id}">{card.id}</td>'
        f'<td data-sort="{name}">{name}</td>'
        f'<td data-sort="{card.rarity.value}" class="rarity-{card.rarity.name}">{card.rarity.name}</td>'
        f'<td data-sort="{html.escape(card.type.value)}">{card.type.value}</td>'
        f'<td data-sort="{card.level}">{card.level}</td>'
        f'<td data-sort="{card.attack}">{card.attack}</td>'
        f'<td data-sort="{int(card.locked)}">{"yes" if card.locked else ""}</td>'
        f'<td data-sort="{int(card.favorite)}">{"★" if card.favorite else ""}</td>'
        "</tr>"
    )


def build_box_report(cards: Sequence[Card], title: str = "Dokkan box") -> str:
    """One self-contained HTML document: the whole box, what `sell auto`
    would sell, and a suggested team — the "various things you can do with
    them" a player would otherwise have to ask for one command at a time."""
    summary = summarize_box(cards)
    sellable_ids = {c.id for c in plan_sale(cards)}
    try:
        team_ids = set(plan_team(cards).card_ids) if cards else set()
    except InvalidTeam:
        team_ids = set()  # e.g. no card satisfies the (open) stage restrictions

    rows = "\n".join(
        _card_row(c, sellable=c.id in sellable_ids, on_team=c.id in team_ids)
        for c in sorted(cards, key=lambda c: (-c.rarity.value, c.name))
    )
    rarity_cells = "".join(f"<div>{r.name}: {n}</div>" for r, n in summary.by_rarity.items())
    type_cells = "".join(f"<div>{t.value}: {n}</div>" for t, n in summary.by_type.items())

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{html.escape(title)}</title>
<style>{_STYLE}</style>
</head>
<body>
<h1>{html.escape(title)}</h1>
<div class="summary">
  <div><strong>{summary.total}</strong> cards</div>
  <div><strong>{summary.sellable}</strong> sellable (shown in amber below)</div>
  <div><strong>{len(team_ids)}</strong>-card suggested team (shown in green below)</div>
</div>
<h2>By rarity</h2>
<div class="summary">{rarity_cells}</div>
<h2>By type</h2>
<div class="summary">{type_cells}</div>
<h2>Cards <span class="note">(click a column to sort)</span></h2>
<table id="cards">
<thead><tr>
<th onclick="sortTable(this.closest('table'),0,true)">ID</th>
<th onclick="sortTable(this.closest('table'),1,false)">Name</th>
<th onclick="sortTable(this.closest('table'),2,true)">Rarity</th>
<th onclick="sortTable(this.closest('table'),3,false)">Type</th>
<th onclick="sortTable(this.closest('table'),4,true)">Level</th>
<th onclick="sortTable(this.closest('table'),5,true)">Attack</th>
<th onclick="sortTable(this.closest('table'),6,true)">Locked</th>
<th onclick="sortTable(this.closest('table'),7,true)">Favorite</th>
</tr></thead>
<tbody>
{rows}
</tbody>
</table>
<p class="note">Generated offline from your collection file — nothing on this
page talks to the game or to the internet. "Sellable" and "suggested team"
mirror what <code>sell auto</code> and <code>team</code> would do; nothing
here has actually been sold or changed.</p>
<script>{_SORT_SCRIPT}</script>
</body>
</html>
"""
