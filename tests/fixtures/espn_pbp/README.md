# ESPN play-by-play fixtures

Real payloads from ESPN's public summary endpoint
(`site.api.espn.com/apis/site/v2/sports/football/nfl/summary?event=<id>`),
captured so the play-by-play tests never touch the network.

## What was stripped

The full payload is ~2 MB per game and mostly irrelevant to parsing. These
keep the exact shape the parser reads, with three reductions:

- top-level keys other than `drives` and `boxscore` (news, standings, videos,
  odds, win probability) are dropped;
- each drive keeps only its `plays` list — no drive-level metadata;
- each play keeps every field **except `teamParticipants`**, which is 40% of a
  play's bytes and carries only team ids, never athletes. It is the reason
  attribution has to parse play text at all.

Nothing a play is parsed *from* was removed.

**Every `boxscore` statistic category is kept, including the defensive ones.**
An earlier capture trimmed to `passing`/`rushing`/`receiving` and silently
destroyed the ambiguity this fixture exists to reproduce: `Chris Williams` is a
defender, so dropping defensive categories left `c.williams` resolving cleanly
to one athlete. A regression fixture that no longer reproduces the regression
is worse than no fixture.

## Why these two games

| Fixture | Why |
|---|---|
| `20251019_NewOrleansSaints_at_ChicagoBears` | `c.williams` resolves to **two** athletes — Caleb Williams and Chris Williams. During the design spike this ambiguity zeroed the passer, and because receivers are credited relative to the passer it silently zeroed Rome Odunze and D'Andre Swift too. The regression fixture for role-based disambiguation. |
| `20250914_JacksonvilleJaguars_at_CincinnatiBengals` | A clean game with touchdown passes and QB sacks — covers the `"Passing Touchdown"` play type (which matches neither `"reception"` nor `"pass complete"`) and sack-vs-rush classification. |

## Refreshing

These are historical completed games, so they are stable. Re-capture only if
ESPN changes its payload shape — which is exactly the change these fixtures
exist to detect.
