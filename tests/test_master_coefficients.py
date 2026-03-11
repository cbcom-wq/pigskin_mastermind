"""Tests for master_coefficients persistence module."""

import json
import os
import pytest
from unittest.mock import patch

from pigskin_mastermind.models.algorithm_coefficients import (
    AlgorithmCoefficients,
    PositionCoefficients,
)
from pigskin_mastermind.services.master_coefficients import (
    load_master_coefficients,
    load_master_coefficients_raw,
    save_master_coefficients,
    reset_master_coefficients,
    get_effective_coefficients,
)


@pytest.fixture
def tmp_master_file(tmp_path):
    """Patch the master coefficients file path to a temp directory."""
    master_file = str(tmp_path / "master_coefficients.json")
    with patch("pigskin_mastermind.services.master_coefficients._MASTER_FILE", master_file), \
         patch("pigskin_mastermind.services.master_coefficients._CONFIG_DIR", str(tmp_path)):
        yield master_file


class TestLoadMasterCoefficients:
    def test_returns_none_when_no_file(self, tmp_master_file):
        result = load_master_coefficients()
        assert result is None

    def test_loads_position_keyed_file(self, tmp_master_file):
        pos_coeffs = PositionCoefficients.from_global()
        data = pos_coeffs.to_dict()
        data["_meta"] = {"accepted_at": "2026-03-08T12:00:00Z"}
        with open(tmp_master_file, "w") as f:
            json.dump(data, f)

        result = load_master_coefficients()
        assert result is not None
        assert isinstance(result, PositionCoefficients)
        # Should have default + per-position entries
        assert result.default.baseline_weight == 1.0

    def test_loads_flat_dict_file(self, tmp_master_file):
        data = {"baseline_weight": 1.5, "skill_multiplier": 0.2, "_meta": {}}
        with open(tmp_master_file, "w") as f:
            json.dump(data, f)

        result = load_master_coefficients()
        assert result is not None
        assert result.default.baseline_weight == 1.5
        assert result.default.skill_multiplier == 0.2


class TestLoadMasterCoefficientsRaw:
    def test_returns_none_when_no_file(self, tmp_master_file):
        result = load_master_coefficients_raw()
        assert result is None

    def test_returns_raw_dict_with_meta(self, tmp_master_file):
        data = {"default": {"baseline_weight": 1.0}, "_meta": {"accepted_at": "2026-03-08"}}
        with open(tmp_master_file, "w") as f:
            json.dump(data, f)

        result = load_master_coefficients_raw()
        assert result is not None
        assert "_meta" in result
        assert result["_meta"]["accepted_at"] == "2026-03-08"


class TestSaveMasterCoefficients:
    def test_saves_position_coefficients(self, tmp_master_file):
        pos_coeffs = PositionCoefficients.from_global(
            AlgorithmCoefficients(baseline_weight=1.3)
        )
        path = save_master_coefficients(
            pos_coeffs,
            source_run_id="test_run_001",
            source_description="Test save",
        )
        assert os.path.isfile(path)

        with open(path) as f:
            data = json.load(f)
        assert data["default"]["baseline_weight"] == 1.3
        assert data["_meta"]["source_run_id"] == "test_run_001"
        assert data["_meta"]["source_description"] == "Test save"
        assert "accepted_at" in data["_meta"]

    def test_saves_flat_dict(self, tmp_master_file):
        path = save_master_coefficients(
            {"baseline_weight": 0.9, "skill_multiplier": 0.15}
        )
        assert os.path.isfile(path)

        with open(path) as f:
            data = json.load(f)
        # Should be normalised into position-keyed format
        assert "default" in data
        assert data["default"]["baseline_weight"] == 0.9

    def test_saves_position_keyed_dict(self, tmp_master_file):
        coeffs = {
            "default": {"baseline_weight": 1.0},
            "QB": {"baseline_weight": 1.2, "skill_multiplier": 0.15},
        }
        path = save_master_coefficients(coeffs, source_run_id="run_123")
        assert os.path.isfile(path)

        with open(path) as f:
            data = json.load(f)
        assert data["QB"]["baseline_weight"] == 1.2
        assert data["_meta"]["source_run_id"] == "run_123"

    def test_raises_on_invalid_type(self, tmp_master_file):
        with pytest.raises(TypeError):
            save_master_coefficients("not a dict")


class TestResetMasterCoefficients:
    def test_returns_false_when_no_file(self, tmp_master_file):
        assert reset_master_coefficients() is False

    def test_removes_file_and_returns_true(self, tmp_master_file):
        save_master_coefficients({"baseline_weight": 1.0})
        assert os.path.isfile(tmp_master_file)
        assert reset_master_coefficients() is True
        assert not os.path.isfile(tmp_master_file)


class TestGetEffectiveCoefficients:
    def test_returns_defaults_when_no_master(self, tmp_master_file):
        result = get_effective_coefficients()
        assert isinstance(result, PositionCoefficients)
        assert result.default.baseline_weight == 1.0  # built-in default

    def test_returns_master_when_saved(self, tmp_master_file):
        save_master_coefficients(
            PositionCoefficients.from_global(
                AlgorithmCoefficients(baseline_weight=1.5)
            )
        )
        result = get_effective_coefficients()
        assert result.default.baseline_weight == 1.5

    def test_round_trip_preserves_position_values(self, tmp_master_file):
        qb_coeffs = AlgorithmCoefficients(baseline_weight=1.1, skill_multiplier=0.2)
        rb_coeffs = AlgorithmCoefficients(baseline_weight=0.9, skill_multiplier=0.08)
        pos_coeffs = PositionCoefficients(
            default=AlgorithmCoefficients(),
            by_position={"QB": qb_coeffs, "RB": rb_coeffs},
        )
        save_master_coefficients(pos_coeffs)
        loaded = get_effective_coefficients()

        assert loaded.get_for_position("QB").baseline_weight == 1.1
        assert loaded.get_for_position("QB").skill_multiplier == 0.2
        assert loaded.get_for_position("RB").baseline_weight == 0.9
        # WR should fall back to default
        assert loaded.get_for_position("WR").baseline_weight == 1.0
