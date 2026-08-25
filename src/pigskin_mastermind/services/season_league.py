"""Turn a finished mock draft into a persisted league.

The draft engine's state is a module-level in-memory dict that does not survive
a restart. This is the one place that reads it and writes something permanent,
so every validation the league depends on happens here, before anything is
committed.
"""

import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from pigskin_mastermind.models.database import (
    DBLeague, DBMatchup, DBRosterSpot, DBTeam,
)
from pigskin_mastermind.services.mock_draft import (
    DEFAULT_LINEUP_SLOTS, draft_engine,
)
from pigskin_mastermind.services.player_identity import PlayerIdentityService
from pigskin_mastermind.services.season_schedule import (
    clamp_playoff_teams, playoff_rounds, regular_season_schedule,
)


class DraftCommitError(ValueError):
    """A draft cannot become a league.

    Carries *unresolved* so the caller can list every unmatched player at once.
    Reporting them one per run would make a 15-round draft unusable to fix.
    """

    def __init__(self, message: str, unresolved: Optional[List[Dict[str, Any]]] = None):
        super().__init__(message)
        self.unresolved = unresolved or []


class SeasonLeagueService:
    """Creates and inspects season leagues."""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.identity = PlayerIdentityService(db)

    def create_from_draft(
        self,
        draft_id: str,
        name: str,
        user_team_name: str,
        owner: str,
        year: Optional[int] = None,
    ) -> DBLeague:
        """Commit a completed draft as a league. One transaction."""
        state = draft_engine.get_draft(draft_id)
        if not state:
            raise DraftCommitError(f"Draft {draft_id} not found")
        if state["status"] != "complete":
            raise DraftCommitError(
                "Draft is not complete; a partial draft has unfilled rosters "
                "and no honest way to schedule",
            )

        num_teams = state["num_teams"]
        if num_teams % 2 != 0:
            raise DraftCommitError(
                f"A league needs an even number of teams; this draft has "
                f"{num_teams}. A round robin over an odd count leaves one team "
                f"idle every week.",
            )

        year = year or datetime.utcnow().year
        resolved = self._resolve_rosters(state)

        # Everything below writes. A failure past this point must leave the
        # database exactly as it found it -- the caller owning the session is
        # not a guarantee, it is a coincidence.
        try:
            league = self._create_league(state, name, year)
            teams = self._create_teams(state, league, user_team_name, owner)
            self._create_roster_spots(league, teams, resolved)
            self._create_schedule(state, league, teams, year)
            self._assert_invariants(state, league, teams)
        except Exception:
            self.db.rollback()
            raise

        self.db.commit()
        return league

    # ------------------------------------------------------------------
    # Steps
    # ------------------------------------------------------------------

    def _resolve_rosters(self, state: Dict[str, Any]) -> Dict[str, List[int]]:
        """Map every drafted pool dict to a DBPlayer id.

        ``db_id`` is present when the pool came from the local ADP table, and
        absent when the draft ran off the live ESPN feed and the name did not
        match. Falling back to PlayerIdentityService is the repo's standing
        rule for every importer — and passing ``nfl_team`` is what lets team
        defenses resolve, since their names never match across sources.
        """
        resolved: Dict[str, List[int]] = {}
        unresolved: List[Dict[str, Any]] = []

        for slot, roster in state["rosters"].items():
            ids: List[int] = []
            for entry in roster:
                player_id = entry.get("db_id")
                if player_id is None:
                    player = self.identity.resolve(
                        name=entry.get("name"),
                        position=entry.get("position"),
                        nfl_team=entry.get("nfl_team"),
                    )
                    player_id = player.id if player else None
                if player_id is None:
                    unresolved.append({
                        "slot": slot,
                        "name": entry.get("name"),
                        "position": entry.get("position"),
                        "nfl_team": entry.get("nfl_team"),
                    })
                else:
                    ids.append(player_id)
            resolved[slot] = ids

        if unresolved:
            raise DraftCommitError(
                f"{len(unresolved)} drafted players could not be matched to the "
                f"player database",
                unresolved=unresolved,
            )
        return resolved

    def _create_league(
        self, state: Dict[str, Any], name: str, year: int,
    ) -> DBLeague:
        num_teams = state["num_teams"]
        playoff_teams = clamp_playoff_teams(num_teams, 6)
        rounds = playoff_rounds(playoff_teams, 15)
        # A smaller bracket gives its unused early weeks back to the regular
        # season rather than finishing the year early.
        regular_weeks = (rounds[0]["week"] - 1) if rounds else 17

        league = DBLeague(
            league_id=f"season-{uuid.uuid4().hex[:12]}",
            name=name,
            year=year,
            kind="season",
            status="in_season",
            current_week=1,
            regular_season_weeks=regular_weeks,
            playoff_teams=playoff_teams,
            playoff_start_week=15,
            roster_slots=state.get("lineup_slots") or dict(DEFAULT_LINEUP_SLOTS),
            draft_snapshot=state.get("picks_log"),
        )
        self.db.add(league)
        self.db.flush()
        return league

    def _create_teams(
        self,
        state: Dict[str, Any],
        league: DBLeague,
        user_team_name: str,
        owner: str,
    ) -> Dict[str, DBTeam]:
        from pigskin_mastermind.entertainment import TeamNameGenerator

        user_slot = str(state["user_pick_position"])
        teams: Dict[str, DBTeam] = {}
        namer = TeamNameGenerator()
        used_names = {user_team_name}

        def _unique_name() -> str:
            """The generator draws from a small word pool, so 11 AI teams
            collide readily. A league with two "Thunder Titans" is confusing
            in every standings table it ever renders."""
            for _ in range(50):
                candidate = namer.generate_random_name()
                if candidate not in used_names:
                    used_names.add(candidate)
                    return candidate
            fallback = f"{namer.generate_random_name()} {len(used_names)}"
            used_names.add(fallback)
            return fallback

        for slot in range(1, state["num_teams"] + 1):
            slot_s = str(slot)
            is_user = slot_s == user_slot
            team = DBTeam(
                team_id=f"{league.league_id}-{slot}",
                name=user_team_name if is_user else _unique_name(),
                owner=owner if is_user else "AI Manager",
                league_id=league.league_id,
                is_user_team=is_user,
                manager_type="human" if is_user else "ai",
                draft_slot=slot,
                ai_strategy=None if is_user else state["strategies"].get(slot_s),
                ai_profile=None if is_user else state.get("ai_profiles", {}).get(slot_s),
                wins=0, losses=0, ties=0, total_points=0.0,
            )
            self.db.add(team)
            teams[slot_s] = team

        self.db.flush()
        return teams

    def _create_roster_spots(
        self,
        league: DBLeague,
        teams: Dict[str, DBTeam],
        resolved: Dict[str, List[int]],
    ) -> None:
        for slot, player_ids in resolved.items():
            team = teams[slot]
            for player_id in player_ids:
                self.db.add(DBRosterSpot(
                    league_id=league.id,
                    team_id=team.id,
                    player_id=player_id,
                    acquired_via="draft",
                ))
        self.db.flush()

    def _create_schedule(
        self,
        state: Dict[str, Any],
        league: DBLeague,
        teams: Dict[str, DBTeam],
        year: int,
    ) -> None:
        ordered = [teams[str(s)] for s in range(1, state["num_teams"] + 1)]
        weeks = regular_season_schedule(len(ordered), league.regular_season_weeks)

        for week_index, pairs in enumerate(weeks, start=1):
            for bracket_slot, (home, away) in enumerate(pairs):
                self.db.add(DBMatchup(
                    league_id=league.id, year=year, week=week_index,
                    bracket_slot=bracket_slot,
                    home_team_id=ordered[home].id,
                    away_team_id=ordered[away].id,
                ))

        for rnd in playoff_rounds(league.playoff_teams, league.playoff_start_week):
            for bracket_slot in range(rnd["games"]):
                self.db.add(DBMatchup(
                    league_id=league.id, year=year, week=rnd["week"],
                    bracket_slot=bracket_slot, is_playoff=True,
                    round_name=rnd["round_name"],
                ))

        self.db.flush()

    def _assert_invariants(
        self, state: Dict[str, Any], league: DBLeague, teams: Dict[str, DBTeam],
    ) -> None:
        """Fail loudly here rather than quietly in week 6."""
        expected_spots = state["num_rounds"]
        for slot, team in teams.items():
            count = (
                self.db.query(DBRosterSpot)
                .filter_by(league_id=league.id, team_id=team.id)
                .count()
            )
            if count != expected_spots:
                raise DraftCommitError(
                    f"Team in slot {slot} has {count} roster spots, "
                    f"expected {expected_spots}",
                )

        team_ids = {t.id for t in teams.values()}
        for week in range(1, league.regular_season_weeks + 1):
            games = (
                self.db.query(DBMatchup)
                .filter_by(league_id=league.id, week=week, is_playoff=False)
                .all()
            )
            playing: List[int] = []
            for game in games:
                if game.home_team_id == game.away_team_id:
                    raise DraftCommitError(f"Week {week} has a team playing itself")
                playing.extend([game.home_team_id, game.away_team_id])
            if sorted(playing) != sorted(team_ids):
                raise DraftCommitError(
                    f"Week {week} does not have every team playing exactly once",
                )
