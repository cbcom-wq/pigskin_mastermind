"""ESPN stat key to internal field name mapping.

Translates ESPN's PLAYER_STATS_MAP keys (from espn_api/football/constant.py)
to our standardized stat field names used in DBPlayerGameLog.
"""

# Maps ESPN breakdown key names to our internal DBPlayerGameLog field names.
# ESPN box_player.breakdown returns a dict like {'rushingYards': 85, 'rushingTouchdowns': 1}
ESPN_TO_INTERNAL = {
    # Passing
    'passingAttempts': 'pass_att',
    'passingCompletions': 'pass_cmp',
    'passingYards': 'pass_yd',
    'passingTouchdowns': 'pass_td',
    'passingInterceptions': 'pass_int',
    'passing2PtConversions': 'two_pt_conversions',

    # Rushing
    'rushingAttempts': 'rush_att',
    'rushingYards': 'rush_yd',
    'rushingTouchdowns': 'rush_td',
    'rushing2PtConversions': 'two_pt_conversions',

    # Receiving
    'receivingTargets': 'targets',
    'receivingReceptions': 'rec',
    'receivingYards': 'rec_yd',
    'receivingTouchdowns': 'rec_td',
    'receiving2PtConversions': 'two_pt_conversions',

    # Misc
    'fumbles': 'fumbles',
    'lostFumbles': 'fumbles_lost',
    '2PtConversions': 'two_pt_conversions',
    'fumbleRecoveredForTD': 'rec_td',  # counted as receiving TD equivalent

    # Kicking
    'madeFieldGoalsFrom50Plus': 'fg_50_plus',
    'madeFieldGoalsFrom40To49': 'fg_40_49',
    'madeFieldGoalsFromUnder40': 'fg_0_39',
    'missedFieldGoals': 'fg_miss',
    'madeExtraPoints': 'xp',

    # Team defense
    'defensiveTouchdowns': 'def_td',
    'defensiveInterceptions': 'def_int',
    'defensiveFumbles': 'def_fumble_rec',
    'defensiveSafeties': 'def_safety',
    'defensiveSacks': 'def_sack',
    'defensivePointsAllowed': 'pts_allowed',
}

# ESPN numeric stat IDs (from PLAYER_STATS_MAP) to our internal field names.
# Used when ESPN returns stats keyed by numeric ID rather than string names.
ESPN_STAT_ID_TO_INTERNAL = {
    0: 'pass_att',
    1: 'pass_cmp',
    3: 'pass_yd',
    4: 'pass_td',
    19: 'two_pt_conversions',
    20: 'pass_int',
    23: 'rush_att',
    24: 'rush_yd',
    25: 'rush_td',
    26: 'two_pt_conversions',
    41: 'rec',
    42: 'rec_yd',
    43: 'rec_td',
    44: 'two_pt_conversions',
    58: 'targets',
    62: 'two_pt_conversions',
    68: 'fumbles',
    72: 'fumbles_lost',

    # Kicking. ESPN splits made field goals into distance buckets, which is
    # exactly the granularity scoring needs — a 52-yarder is worth more than
    # a 21-yarder, so a single 'fg' key would lose the distinction.
    74: 'fg_50_plus',   # madeFieldGoalsFrom50Plus
    77: 'fg_40_49',     # madeFieldGoalsFrom40To49
    80: 'fg_0_39',      # madeFieldGoalsFromUnder40
    85: 'fg_miss',      # missedFieldGoals
    86: 'xp',           # madeExtraPoints

    # Team defense.
    94: 'def_td',           # defensiveTouchdowns
    95: 'def_int',          # defensiveInterceptions
    96: 'def_fumble_rec',   # defensiveFumbles (recoveries)
    98: 'def_safety',       # defensiveSafeties
    99: 'def_sack',         # defensiveSacks
    120: 'pts_allowed',     # defensivePointsAllowed
}


def map_espn_breakdown_to_stats(breakdown: dict) -> dict:
    """Convert an ESPN breakdown dict to our internal stat field names.

    Args:
        breakdown: ESPN box_player.breakdown dict with string keys
                   e.g. {'rushingYards': 85, 'rushingTouchdowns': 1}

    Returns:
        Dict with our internal field names and values summed where
        multiple ESPN keys map to the same field (e.g. 2pt conversions).
    """
    stats = {}
    for espn_key, value in breakdown.items():
        internal_key = ESPN_TO_INTERNAL.get(espn_key)
        if internal_key:
            # Sum values when multiple ESPN keys map to same internal field
            current = stats.get(internal_key, 0)
            stats[internal_key] = current + _to_int(value)
    return stats


def map_espn_stat_ids_to_stats(stat_dict: dict) -> dict:
    """Convert an ESPN numeric stat ID dict to our internal stat field names.

    Args:
        stat_dict: Dict keyed by ESPN numeric stat IDs
                   e.g. {3: 250.0, 4: 2.0, 20: 1.0}

    Returns:
        Dict with our internal field names.
    """
    stats = {}
    for stat_id, value in stat_dict.items():
        stat_id_int = int(stat_id) if isinstance(stat_id, str) else stat_id
        internal_key = ESPN_STAT_ID_TO_INTERNAL.get(stat_id_int)
        if internal_key:
            current = stats.get(internal_key, 0)
            stats[internal_key] = current + _to_int(value)
    return stats


def _to_int(value) -> int:
    """Safely convert a value to int, rounding floats."""
    try:
        return int(round(float(value)))
    except (ValueError, TypeError):
        return 0
