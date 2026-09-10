"""ESPN play-by-play source.

ESPN's summary endpoint is the play-by-play source because it is live, fast,
and needs nothing but ``requests`` -- where the nflverse path costs ~3.5s a
view, publishes after the fact, and needs ``pyarrow``.

The trade is that ESPN plays carry no ``participants`` array, so the actor has
to be parsed out of the play text and matched against the game's own boxscore
roster.  These tests pin both halves against real captured payloads.
"""

import json
import os

import pytest

from pigskin_mastermind.services.play_by_play.espn_source import (
    ESPNPlayByPlaySource,
    plays_from_summary,
)

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "espn_pbp")
BEARS = "20251019_NewOrleansSaints_at_ChicagoBears.json"
JAGS = "20250914_JacksonvilleJaguars_at_CincinnatiBengals.json"


def load(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture
def bears():
    return load(BEARS)


@pytest.fixture
def jags():
    return load(JAGS)


class TestPlayDerivation:
    def test_every_play_carries_the_fields_the_animation_needs(self, jags):
        plays = plays_from_summary(jags)

        assert len(plays) > 100
        for p in plays:
            assert p.play_id
            assert p.description
            assert p.quarter in (1, 2, 3, 4, 5)

    def test_field_position_comes_from_yards_to_endzone(self, jags):
        plays = plays_from_summary(jags)

        positioned = [p for p in plays if p.yardline_100 is not None]
        assert positioned, "no play carried a field position"
        for p in positioned:
            assert 0 <= p.yardline_100 <= 100

    def test_a_touchdown_pass_is_a_completed_pass(self, jags):
        """ESPN types a scoring pass "Passing Touchdown".

        It matches neither "reception" nor "pass complete", so a naive
        completion test drops every scoring pass out of the stat line -- which
        is exactly what happened during the design spike.
        """
        tds = [
            p
            for p in plays_from_summary(jags)
            if (p.play_type or "").lower() == "passing touchdown"
        ]

        assert tds, "fixture carries no touchdown pass"
        for p in tds:
            assert p.complete_pass is True
            assert p.touchdown is True
            assert p.role == "pass"

    def test_a_sack_is_not_a_rush(self, jags):
        """A sack's lost yardage belongs to the passing game.

        Filing it as a rush gives quarterbacks negative rushing yards.
        """
        sacks = [
            p for p in plays_from_summary(jags) if (p.play_type or "").lower() == "sack"
        ]

        assert sacks, "fixture carries no sack"
        for p in sacks:
            assert p.sack is True
            assert p.role == "pass"

    def test_a_penalty_play_keeps_espns_net_yardage(self, jags):
        """The animation wants net field movement, not statistical credit.

        On "T.Lawrence scrambles left end to CIN 19 for 3 yards ... PENALTY
        ... Illegal Forward Pass, 5 yards", ESPN's statYardage is -7 while the
        boxscore credits the rush +4.  Both are right about different
        questions.  The marker on the field moves by the net, so the net is
        what a Play carries -- which is also why rebuilt stat lines will never
        reconcile to the boxscore on compound penalty plays.
        """
        compound = [
            p
            for p in plays_from_summary(jags)
            if "PENALTY" in p.description and "scrambles" in p.description
        ]

        assert compound, "fixture carries no compound penalty play"
        assert any(p.yards_gained < 0 for p in compound)

    def test_epa_is_absent_rather_than_zero(self, jags):
        """ESPN publishes no EPA, and 0.0 is a real EPA value.

        Defaulting to zero would state something false in the same shape as
        the truth.
        """
        assert all(p.epa is None for p in plays_from_summary(jags))


class TestAttribution:
    def test_resolves_the_actor_on_a_rush(self, jags):
        rushes = [p for p in plays_from_summary(jags) if p.role == "rush"]

        attributed = [p for p in rushes if p.actor_ids]
        assert len(attributed) / len(rushes) > 0.9

    def test_credits_passer_then_receiver_in_role_order(self, jags):
        completions = [
            p
            for p in plays_from_summary(jags)
            if p.role == "pass" and p.complete_pass and not p.sack
        ]

        two_party = [p for p in completions if len(p.actor_ids) == 2]
        assert two_party, "no completion resolved both passer and receiver"
        for p in two_party:
            passer, receiver = p.actor_names
            assert passer != receiver

    def test_ambiguous_initials_are_resolved_by_role(self, bears):
        """``c.williams`` is Caleb Williams (QB) and Chris Williams (defender).

        Dropping the ambiguous token voided the passer -- and because a
        receiver is credited relative to the passer, it silently voided every
        receiver on those plays too.  One ambiguous name zeroed a team's whole
        passing game during the design spike.
        """
        passes = [
            p
            for p in plays_from_summary(bears)
            if p.role == "pass" and "C.Williams" in p.description
        ]

        assert passes, "fixture carries no C.Williams pass"
        attributed = [p for p in passes if p.actor_names]
        assert attributed, "the ambiguous passer was dropped entirely"
        assert all(p.actor_names[0] == "Caleb Williams" for p in attributed)

    def test_the_receiver_survives_an_ambiguous_passer(self, bears):
        """The cascade, stated directly: an ambiguous passer must not take
        unambiguous receivers down with it."""
        completions = [
            p
            for p in plays_from_summary(bears)
            if p.complete_pass and "C.Williams" in p.description
        ]

        assert completions
        assert any(len(p.actor_ids) == 2 for p in completions)

    def test_an_unresolvable_actor_is_left_empty_not_guessed(self, jags):
        """A play drawn without a name is recoverable.  A play drawn under the
        wrong name is not."""
        for p in plays_from_summary(jags):
            assert len(p.actor_ids) == len(p.actor_names)
            assert all(a for a in p.actor_ids)


def _stat_lines(plays):
    """Per-athlete yardage rebuilt from parsed plays alone."""
    acc = {}

    def bump(aid, key, amount):
        acc.setdefault(aid, {}).setdefault(key, 0)
        acc[aid][key] += amount

    for p in plays:
        if not p.actor_ids:
            continue
        if p.role == "rush":
            bump(p.actor_ids[0], "rush_yds", int(p.yards_gained))
        elif p.role == "pass" and p.complete_pass and not p.sack:
            bump(p.actor_ids[0], "pass_yds", int(p.yards_gained))
            if len(p.actor_ids) == 2:
                bump(p.actor_ids[1], "rec_yds", int(p.yards_gained))
    return acc


def _boxscore_truth(summary):
    """ESPN's own per-athlete totals from the same payload."""
    wanted = {
        "passing": ("pass_yds", "passingYards"),
        "rushing": ("rush_yds", "rushingYards"),
        "receiving": ("rec_yds", "receivingYards"),
    }
    truth = {}
    for team in (summary.get("boxscore") or {}).get("players") or []:
        for category in team.get("statistics") or []:
            name = (category.get("name") or "").lower()
            if name not in wanted:
                continue
            key, source = wanted[name]
            columns = category.get("keys") or []
            for entry in category.get("athletes") or []:
                athlete = entry.get("athlete") or {}
                stats = dict(zip(columns, entry.get("stats") or []))
                try:
                    value = int(str(stats.get(source, 0)).split("/")[0] or 0)
                except ValueError:
                    value = 0
                truth.setdefault(str(athlete.get("id")), {})[key] = value
                truth[str(athlete.get("id"))]["name"] = athlete.get("displayName")
    return truth


class TestSelfValidation:
    """The strongest available check: the payload grades our own parsing.

    If stat lines rebuilt from the plays we parsed match the boxscore in the
    same payload, then attribution put the right yards on the right players.
    """

    @pytest.mark.parametrize("fixture", [BEARS, JAGS])
    def test_rebuilt_stat_lines_match_espns_own_boxscore(self, fixture):
        summary = load(fixture)
        mine = _stat_lines(plays_from_summary(summary))
        truth = _boxscore_truth(summary)

        exact = off = 0
        misses = []
        for athlete_id, totals in truth.items():
            for key in ("pass_yds", "rush_yds", "rec_yds"):
                if key not in totals:
                    continue
                got = mine.get(athlete_id, {}).get(key, 0)
                if got == totals[key]:
                    exact += 1
                else:
                    off += 1
                    misses.append(
                        "%s %s: rebuilt=%s espn=%s"
                        % (totals.get("name"), key, got, totals[key])
                    )

        rate = exact / (exact + off)
        assert rate >= 0.80, "stat-line agreement %.1f%%\n  %s" % (
            100 * rate,
            "\n  ".join(misses[:12]),
        )


class _FakeClient:
    """Stands in for BoxScoreClient -- the seam live_scoring already uses."""

    def __init__(self, events, summaries):
        self.events = events
        self.summaries = summaries
        self.summary_calls = []

    def week_events(self, year, week):
        return self.events

    def event_summary(self, event_id):
        self.summary_calls.append(event_id)
        return self.summaries.get(event_id)


def _event(event_id, home, away):
    """A ``fetch_week_events`` row.

    That helper flattens ESPN's scoreboard rather than passing it through, so
    this is the real contract -- a fake shaped like raw ESPN JSON passes its
    tests and then finds zero games against the live API.
    """
    return {
        "event_id": event_id,
        "status": "post",
        "home_team": home,
        "away_team": away,
    }


class TestESPNPlayByPlaySource:
    def test_finds_the_game_a_team_played_that_week(self, jags):
        client = _FakeClient(
            events=[_event("1", "KC", "BUF"), _event("2", "CIN", "JAX")],
            summaries={"2": jags},
        )

        plays = ESPNPlayByPlaySource(client=client).plays(2025, 2, "JAX")

        assert client.summary_calls == ["2"]
        assert len(plays) > 100

    def test_a_team_not_playing_that_week_yields_no_plays(self, jags):
        """A bye, or a week that has not happened.  Empty is an answer -- it
        must not raise, and it must not fetch a summary it has no reason to."""
        client = _FakeClient(events=[_event("1", "KC", "BUF")], summaries={"1": jags})

        plays = ESPNPlayByPlaySource(client=client).plays(2025, 2, "JAX")

        assert plays == []
        assert client.summary_calls == []

    def test_an_unavailable_summary_yields_no_plays(self):
        """ESPN reachable but the game's detail is missing -- degrade to empty
        rather than raising into the request."""
        client = _FakeClient(events=[_event("2", "CIN", "JAX")], summaries={})

        assert ESPNPlayByPlaySource(client=client).plays(2025, 2, "JAX") == []


class TestPossessionTeam:
    """Who had the ball, needed for a full-game view's posteam/defteam.

    ESPN puts a numeric team id on the play (``start.team.id``); the
    abbreviation lives in the payload's own team blocks.  A play is useless to
    a whole-game animation without it -- there is no single "the player" to
    infer possession from, the way a one-player view can.
    """

    def test_plays_carry_the_team_with_the_ball(self, jags):
        plays = plays_from_summary(jags)

        withteam = [p for p in plays if p.possession_team]
        assert len(withteam) / len(plays) > 0.9
        assert {p.possession_team for p in withteam} == {"JAX", "CIN"}

    def test_possession_matches_the_text(self, jags):
        """A play describing a Jaguars passer belongs to Jacksonville."""
        lawrence = [
            p for p in plays_from_summary(jags) if "T.Lawrence pass" in p.description
        ]

        assert lawrence
        assert all(p.possession_team == "JAX" for p in lawrence)

    def test_home_and_away_are_available_for_the_game(self, jags):
        from pigskin_mastermind.services.play_by_play.espn_source import (
            teams_from_summary,
        )

        assert teams_from_summary(jags) == {"home": "CIN", "away": "JAX"}


class TestSpecialTeamsAndDeadBall:
    """A whole-game view shows more than scrimmage plays.

    The player animation only ever cared about pass and rush, because those
    are the plays a skill player appears in.  A full-game timeline also has
    kickoffs, punts, kicks and dead-ball administration, and lumping them into
    "other" makes the timeline unreadable.
    """

    @pytest.mark.parametrize(
        "type_text,expected",
        [
            ("Kickoff", "kickoff"),
            ("Punt", "punt"),
            ("Field Goal Good", "field_goal"),
            ("Field Goal Missed", "field_goal"),
            ("Timeout", "no_play"),
            ("Official Timeout", "no_play"),
            ("End Period", "no_play"),
            ("End of Game", "no_play"),
            ("Two-minute warning", "no_play"),
            ("Penalty", "no_play"),
        ],
    )
    def test_type_maps_to_a_role(self, type_text, expected):
        from pigskin_mastermind.services.play_by_play.espn_source import _classify

        assert _classify(type_text, "")["role"] == expected

    def test_scrimmage_roles_are_unchanged(self):
        """The player view depends on these; special teams must not disturb it."""
        from pigskin_mastermind.services.play_by_play.espn_source import _classify

        assert _classify("Pass Reception", "")["role"] == "pass"
        assert _classify("Passing Touchdown", "")["role"] == "pass"
        assert _classify("Sack", "")["role"] == "pass"
        assert _classify("Rush", "")["role"] == "rush"
        assert _classify("Rushing Touchdown", "")["role"] == "rush"

    def test_a_kickoff_is_not_attributed_as_a_rusher(self, jags):
        """Kick returns name a player, but he is not a ball carrier on a
        scrimmage play and must not land in a rushing line."""
        kickoffs = [p for p in plays_from_summary(jags) if p.role == "kickoff"]

        assert kickoffs
        assert all(p.actor_ids == [] for p in kickoffs)
