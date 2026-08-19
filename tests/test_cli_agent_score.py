"""Pinning tests for the plain functions backing `pigskin agent score`.

These do not drive the Click command itself (there is no CliRunner
precedent in this repo) -- they pin the pure warning helper the command
calls, which is the part with a decision to get right.
"""

from pigskin_mastermind.cli import (
    _FULL_SEASON_GAMES,
    _season_completeness_warning,
)


def test_no_warning_for_weekly_scope():
    assert _season_completeness_warning(None) is None


def test_no_warning_for_a_complete_season():
    assert _season_completeness_warning(_FULL_SEASON_GAMES) is None


def test_no_warning_for_a_season_missing_only_a_bye_or_two():
    # 15.5 of 17 is above the warning threshold (17 * 0.85 == 14.45).
    assert _season_completeness_warning(15.5) is None


def test_warns_for_a_materially_partial_season():
    warning = _season_completeness_warning(9.0)
    assert warning is not None
    assert "9.0" in warning
    assert "17" in warning
