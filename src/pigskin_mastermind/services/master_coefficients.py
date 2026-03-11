"""Master coefficients persistence — save and load the active tuned values.

The master coefficients are stored as a single JSON file at
``~/.pigskin_mastermind/master_coefficients.json``.  When no file exists
every call falls through to the hard-coded defaults in
:class:`~pigskin_mastermind.models.algorithm_coefficients.AlgorithmCoefficients`.

The file format is the same as ``PositionCoefficients.to_dict()``::

    {
        "default": { "baseline_weight": 1.0, ... },
        "QB": { "baseline_weight": 1.05, ... },
        ...
    }

A flat dict (legacy format) is also accepted and will be expanded via
``PositionCoefficients.from_dict()``.

Metadata about *when* and *from which tuning run* the coefficients were
accepted is stored alongside the values under the ``"_meta"`` key.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from pigskin_mastermind.models.algorithm_coefficients import (
    AlgorithmCoefficients,
    PositionCoefficients,
)

_CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".pigskin_mastermind")
_MASTER_FILE = os.path.join(_CONFIG_DIR, "master_coefficients.json")


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


def load_master_coefficients() -> Optional[PositionCoefficients]:
    """Load persisted master coefficients, or ``None`` if none saved.

    Returns ``None`` when no master file exists — callers should fall back
    to the hard-coded defaults in that case.
    """
    if not os.path.isfile(_MASTER_FILE):
        return None
    with open(_MASTER_FILE, "r") as fh:
        raw = json.load(fh)

    # Strip metadata key before building coefficients
    data = {k: v for k, v in raw.items() if k != "_meta"}
    if not data:
        return None

    return PositionCoefficients.from_dict(data)


def load_master_coefficients_raw() -> Optional[Dict[str, Any]]:
    """Load the raw JSON (including ``_meta``) for API responses."""
    if not os.path.isfile(_MASTER_FILE):
        return None
    with open(_MASTER_FILE, "r") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------


def save_master_coefficients(
    coefficients: PositionCoefficients | Dict[str, Any],
    *,
    source_run_id: Optional[str] = None,
    source_description: Optional[str] = None,
) -> str:
    """Persist *coefficients* as the new master values.

    Args:
        coefficients: A :class:`PositionCoefficients` instance **or** a
            dict in the same format (``{"default": {…}, "QB": {…}, …}``
            or flat ``{key: float}``).
        source_run_id: Optional tuning-run ID that produced these values.
        source_description: Free-text note attached to the metadata.

    Returns:
        The file path where the master coefficients were written.
    """
    os.makedirs(_CONFIG_DIR, exist_ok=True)

    if isinstance(coefficients, PositionCoefficients):
        data = coefficients.to_dict()
    elif isinstance(coefficients, dict):
        # Normalise through the model so we always get a clean structure
        pos_coeffs = PositionCoefficients.from_dict(coefficients)
        data = pos_coeffs.to_dict()
    else:
        raise TypeError(
            f"Expected PositionCoefficients or dict, got {type(coefficients).__name__}"
        )

    # Attach metadata
    data["_meta"] = {
        "accepted_at": datetime.now(timezone.utc).isoformat(),
        "source_run_id": source_run_id,
        "source_description": source_description,
    }

    with open(_MASTER_FILE, "w") as fh:
        json.dump(data, fh, indent=2)

    return _MASTER_FILE


# ---------------------------------------------------------------------------
# Reset
# ---------------------------------------------------------------------------


def reset_master_coefficients() -> bool:
    """Delete the master coefficients file, reverting to hard-coded defaults.

    Returns ``True`` if a file was removed, ``False`` if none existed.
    """
    if os.path.isfile(_MASTER_FILE):
        os.remove(_MASTER_FILE)
        return True
    return False


# ---------------------------------------------------------------------------
# Convenience: effective coefficients
# ---------------------------------------------------------------------------


def get_effective_coefficients() -> PositionCoefficients:
    """Return master coefficients if saved, otherwise built-in defaults.

    This is the single function production code should call to obtain the
    active coefficient set.
    """
    master = load_master_coefficients()
    if master is not None:
        return master
    return PositionCoefficients.from_global()
