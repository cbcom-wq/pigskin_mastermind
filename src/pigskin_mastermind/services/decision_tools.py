"""Decision-making tools for fantasy football."""

from typing import List, Dict, Any, Optional
from pigskin_mastermind.models.team import Team
from pigskin_mastermind.models.player import Player


class LineupOptimizer:
    """
    Optimize fantasy football lineups.
    
    Provides tools for selecting the best starting lineup based on projections.
    """
    
    def __init__(self, lineup_rules: Optional[Dict[str, int]] = None):
        """
        Initialize the lineup optimizer.
        
        Args:
            lineup_rules: Dictionary defining lineup slots
        """
        self.lineup_rules = lineup_rules or {
            'QB': 1,
            'RB': 2,
            'WR': 2,
            'TE': 1,
            'FLEX': 1,  # RB/WR/TE
            'K': 1,
            'DEF': 1,
        }
    
    def optimize_lineup(self, team: Team) -> Dict[str, Any]:
        """
        Generate the optimal starting lineup for a team.
        
        Args:
            team: Team instance
            
        Returns:
            Dictionary with optimal lineup and analysis
        """
        lineup = {}
        bench = []
        used_players = set()
        
        # Fill required positions
        for position, count in self.lineup_rules.items():
            if position == 'FLEX':
                continue
            
            position_players = sorted(
                [p for p in team.get_players_by_position(position) if p.player_id not in used_players],
                key=lambda p: p.projected_points,
                reverse=True
            )
            
            lineup[position] = position_players[:count]
            for player in lineup[position]:
                used_players.add(player.player_id)
        
        # Fill FLEX position with best remaining RB/WR/TE
        if 'FLEX' in self.lineup_rules:
            flex_positions = ['RB', 'WR', 'TE']
            flex_candidates = []
            for pos in flex_positions:
                flex_candidates.extend([
                    p for p in team.get_players_by_position(pos)
                    if p.player_id not in used_players
                ])
            
            flex_candidates.sort(key=lambda p: p.projected_points, reverse=True)
            if flex_candidates:
                lineup['FLEX'] = [flex_candidates[0]]
                used_players.add(flex_candidates[0].player_id)
            else:
                lineup['FLEX'] = []
        
        # Remaining players go to bench
        bench = [p for p in team.players if p.player_id not in used_players]
        
        total_projected = sum(
            p.projected_points
            for position_list in lineup.values()
            for p in position_list
        )
        
        return {
            'lineup': lineup,
            'bench': bench,
            'total_projected_points': total_projected,
            'positions_filled': sum(len(players) for players in lineup.values()),
        }
    
    def suggest_lineup_changes(self, team: Team, current_lineup: List[Player]) -> List[Dict[str, Any]]:
        """
        Suggest improvements to a lineup.
        
        Args:
            team: Team instance
            current_lineup: List of players in current lineup
            
        Returns:
            List of suggested changes
        """
        suggestions = []
        current_ids = {p.player_id for p in current_lineup}
        bench_players = [p for p in team.players if p.player_id not in current_ids]
        
        for bench_player in bench_players:
            for lineup_player in current_lineup:
                if bench_player.position == lineup_player.position:
                    if bench_player.projected_points > lineup_player.projected_points:
                        point_gain = bench_player.projected_points - lineup_player.projected_points
                        suggestions.append({
                            'action': 'swap',
                            'bench_out': lineup_player.name,
                            'bench_in': bench_player.name,
                            'position': bench_player.position,
                            'projected_gain': point_gain,
                        })
        
        suggestions.sort(key=lambda s: s['projected_gain'], reverse=True)
        return suggestions


class TradeAnalyzer:
    """
    Analyze fantasy football trades.
    
    Provides tools for evaluating trade fairness and impact.
    """
    
    def analyze_trade(
        self,
        team1: Team,
        team1_gives: List[Player],
        team1_receives: List[Player],
        team2: Team,
        team2_gives: List[Player],
        team2_receives: List[Player],
    ) -> Dict[str, Any]:
        """
        Analyze a proposed trade between two teams.
        
        Args:
            team1: First team
            team1_gives: Players team1 is trading away
            team1_receives: Players team1 is receiving
            team2: Second team
            team2_gives: Players team2 is trading away
            team2_receives: Players team2 is receiving
            
        Returns:
            Dictionary with trade analysis
        """
        # Calculate value for team1
        team1_gives_value = sum(p.projected_points for p in team1_gives)
        team1_receives_value = sum(p.projected_points for p in team1_receives)
        team1_net = team1_receives_value - team1_gives_value
        
        # Calculate value for team2
        team2_gives_value = sum(p.projected_points for p in team2_gives)
        team2_receives_value = sum(p.projected_points for p in team2_receives)
        team2_net = team2_receives_value - team2_gives_value
        
        # Trade fairness (closer to 0 is more fair)
        fairness_score = abs(team1_net - team2_net)
        
        return {
            'team1': {
                'name': team1.name,
                'gives': [p.name for p in team1_gives],
                'gives_value': team1_gives_value,
                'receives': [p.name for p in team1_receives],
                'receives_value': team1_receives_value,
                'net_gain': team1_net,
            },
            'team2': {
                'name': team2.name,
                'gives': [p.name for p in team2_gives],
                'gives_value': team2_gives_value,
                'receives': [p.name for p in team2_receives],
                'receives_value': team2_receives_value,
                'net_gain': team2_net,
            },
            'fairness_score': fairness_score,
            'is_fair': fairness_score < 5.0,  # Threshold for fairness
            'winner': team1.name if team1_net > team2_net else team2.name if team2_net > team1_net else 'Even',
        }
    
    def evaluate_trade_for_team(self, team: Team, gives: List[Player], receives: List[Player]) -> Dict[str, Any]:
        """
        Evaluate a trade from one team's perspective.
        
        Args:
            team: Team making the trade
            gives: Players being traded away
            receives: Players being received
            
        Returns:
            Dictionary with evaluation
        """
        gives_value = sum(p.projected_points for p in gives)
        receives_value = sum(p.projected_points for p in receives)
        net_gain = receives_value - gives_value
        
        # Check positional needs
        positions_lost = {}
        positions_gained = {}
        
        for player in gives:
            positions_lost[player.position] = positions_lost.get(player.position, 0) + 1
        
        for player in receives:
            positions_gained[player.position] = positions_gained.get(player.position, 0) + 1
        
        recommendation = "Accept" if net_gain > 0 else "Reject" if net_gain < -2 else "Consider"
        
        return {
            'team_name': team.name,
            'gives': [p.name for p in gives],
            'receives': [p.name for p in receives],
            'gives_value': gives_value,
            'receives_value': receives_value,
            'net_gain': net_gain,
            'positions_lost': positions_lost,
            'positions_gained': positions_gained,
            'recommendation': recommendation,
        }
