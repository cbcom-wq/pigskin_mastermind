"""Claiming a team re-renders its card.

The claim button targets ``closest .card-hover`` with ``hx-swap="outerHTML"``,
so whatever the endpoint returns *becomes* the card. Returning an empty toast
response therefore deletes the card from the page until the next full reload —
the claim itself lands, but the team visibly disappears from the league grid.
"""

import json
import re

import pytest
from fastapi.testclient import TestClient

from pigskin_mastermind.api.main import app
from pigskin_mastermind.models.database import DBLeague, DBTeam

client = TestClient(app)


@pytest.fixture
def espn_league(db):
    lg = DBLeague(league_id="99887766", name="Airframe Engine League",
                  year=2026, kind="espn", swid="{x}", espn_s2="s2")
    db.add(lg)
    db.commit()

    teams = [
        DBTeam(team_id=f"espn_99887766_{n}", espn_team_id=str(n),
               name=name, owner=owner, league_id="99887766",
               wins=0, losses=0, ties=0, total_points=0.0, is_user_team=False)
        for n, name, owner in [
            (1, "55 burgers 55 fries 55 TDs", "Brandon__COOK"),
            (2, "Burrowhead Stadium", "Ryan Bruns"),
        ]
    ]
    db.add_all(teams)
    db.commit()
    return lg


def test_claim_returns_the_rerendered_card(espn_league, db):
    resp = client.post("/leagues/99887766/teams/espn_99887766_1/claim")

    assert resp.status_code == 200
    # The response replaces the card, so it must BE a card.
    assert resp.text.strip(), "empty response would delete the card from the grid"
    assert "card-hover" in resp.text
    assert "55 burgers 55 fries 55 TDs" in resp.text
    # ...now showing as claimed, with the button flipped to "unclaim".
    assert "My Team" in resp.text
    assert "Unclaim this team" in resp.text


def test_claim_still_reports_the_toast(espn_league, db):
    resp = client.post("/leagues/99887766/teams/espn_99887766_1/claim")

    trigger = json.loads(resp.headers["HX-Trigger"])
    assert "Claimed" in trigger["showToast"]["message"]


def test_unclaim_returns_the_card_in_the_unclaimed_state(espn_league, db):
    client.post("/leagues/99887766/teams/espn_99887766_1/claim")
    resp = client.post("/leagues/99887766/teams/espn_99887766_1/claim")

    assert "card-hover" in resp.text
    assert "My Team" not in resp.text
    assert "Claim as my team" in resp.text
    trigger = json.loads(resp.headers["HX-Trigger"])
    assert "Unclaimed" in trigger["showToast"]["message"]


def test_claimed_team_still_renders_in_the_full_grid(espn_league, db):
    """The regression the user saw: 10 teams in the header, 9 cards below."""
    client.post("/leagues/99887766/teams/espn_99887766_1/claim")

    page = client.get("/leagues/99887766").text
    assert page.count('class="bg-white rounded-xl shadow-sm border border-slate-200 overflow-hidden card-hover') == 2
    assert "55 burgers 55 fries 55 TDs" in page
