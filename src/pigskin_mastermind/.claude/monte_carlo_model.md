You are a senior data scientist and Python engineer building a fantasy football Monte Carlo simulation engine.

Your task is to design and implement a simulation-based model that predicts weekly fantasy football points for a player in 0.5 PPR scoring.

The system should simulate a player’s performance 10,000+ times and produce a distribution of fantasy outcomes.

Objective

Create a Python module that predicts a player's weekly fantasy score using Monte Carlo simulation based on input metrics.

The simulation should model:

• volume (touches / targets)
• efficiency (yards / fantasy points per touch)
• touchdowns
• contextual modifiers (matchup, weather, momentum, injuries)

The output should be a probability distribution of fantasy scores, not just a single prediction.

Scoring System

Use 0.5 PPR scoring:

0.1 points per yard

6 points per touchdown

0.5 points per reception

Input Features

The model receives the following normalized metrics (0–1 unless otherwise stated):

player_skill_level
team_offense_level
opponent_defense_level
positional_touch_percentage
recent_trend_score
historical_average_points
fantasy_points_per_touch
injury_risk_score
opposing_defense_vs_position_rank   # 1–32
offensive_momentum_score
weather_impact_score
Model Architecture

The simulation should follow this pipeline:

Estimate expected touches

Estimate efficiency per touch

Estimate touchdown probability

Apply context multipliers

Run Monte Carlo simulations

Aggregate the results

Step 1 — Estimate Expected Touches

Estimate baseline touches using historical production and efficiency.

base_touches = historical_average_points / fantasy_points_per_touch

Then apply modifiers:

expected_touches =
    base_touches
    * positional_touch_percentage
    * (1 + recent_trend_score * 0.2)
    * (1 + offensive_momentum_score * 0.1)

Touches should be simulated using a normal distribution.

Example:

touches ~ Normal(mean=expected_touches, std=expected_touches * 0.25)

Clamp touches to a minimum of 0.

Step 2 — Efficiency Per Touch

Base efficiency comes from:

fantasy_points_per_touch

Adjust efficiency based on matchup:

matchup_adjustment =
    (team_offense_level - opponent_defense_level) * 0.1

Add positional matchup:

position_matchup_bonus =
    (17 - opposing_defense_vs_position_rank) / 50

Efficiency multiplier:

efficiency =
    fantasy_points_per_touch
    * (1 + matchup_adjustment)
    * (1 + position_matchup_bonus)

Simulate efficiency with variance:

efficiency ~ Normal(mean=efficiency, std=efficiency * 0.2)
Step 3 — Touchdown Probability

Touchdowns should be simulated using a Poisson distribution.

Example baseline:

td_lambda = 0.6

Modify lambda using skill and offense:

td_lambda =
    0.6
    * (1 + player_skill_level * 0.3)
    * (1 + team_offense_level * 0.2)

Simulate:

touchdowns ~ Poisson(td_lambda)
Step 4 — Context Modifiers

Apply contextual multipliers:

Weather:

weather_multiplier = 1 + weather_impact_score

Injury risk:

Simulate partial usage loss:

if random() < injury_risk_score:
    touches *= 0.5

Momentum:

momentum_multiplier = 1 + offensive_momentum_score * 0.1
Step 5 — Monte Carlo Simulation

Run 10,000 simulations.

Each simulation:

Sample touches

Sample efficiency

Sample touchdowns

Compute fantasy score

Example:

points =
    touches * efficiency
    + touchdowns * 6

Store results in an array.

Step 6 — Output Metrics

After simulations compute:

expected_points = mean(results)
median_points
floor = 10th percentile
ceiling = 90th percentile
boom_probability (points > 25)
bust_probability (points < 8)

Return these in a structured dictionary.

Example output:

{
  "expected_points": 16.3,
  "median": 15.1,
  "floor": 7.9,
  "ceiling": 26.4,
  "boom_probability": 0.21,
  "bust_probability": 0.18
}
Implementation Requirements

Write clean, modular Python code using:

numpy
scipy
dataclasses

The module should include:

FantasyPlayerInput (dataclass)
FantasySimulationEngine (class)
run_simulation()
calculate_summary_stats()

The simulation should be deterministic when given a random seed.

Example Usage
player = FantasyPlayerInput(...)

engine = FantasySimulationEngine(simulations=10000)

result = engine.simulate(player)

print(result)
Additional Design Goals

Structure the system so it can later support:

• QB / RB / WR / TE position-specific models
• team-level game simulations
• Vegas betting line inputs
• correlation between players (QB + WR stacks)

Generate the full Python implementation with comments explaining the model assumptions.