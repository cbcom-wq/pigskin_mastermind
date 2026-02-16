"""Service for generating year-to-date season visualizations."""

import io
import base64
from typing import Dict, List, Any, Optional
from sqlalchemy.orm import Session
from sqlalchemy import func

from pigskin_mastermind.models.database import (
    DBTeam, DBPlayer, DBWeeklyTeamStats, DBWeeklyPlayerStats
)

import matplotlib
matplotlib.use('Agg')  # Use non-interactive backend
import matplotlib.pyplot as plt
import matplotlib.animation as animation
from matplotlib.patches import Rectangle


class SeasonVisualizationService:
    """Service for creating year-to-date season visualizations."""
    
    def __init__(self, db: Session):
        """Initialize the visualization service."""
        self.db = db
    
    def get_season_data(self, team_id: int) -> Dict[str, Any]:
        """
        Get aggregated season data for visualization.
        
        Args:
            team_id: Team ID to get data for
            
        Returns:
            Dictionary with season data formatted for visualization
        """
        team = self.db.query(DBTeam).filter(DBTeam.id == team_id).first()
        if not team:
            return None
        
        # Get weekly team stats ordered by week
        weekly_stats = (
            self.db.query(DBWeeklyTeamStats)
            .filter(DBWeeklyTeamStats.team_id == team_id)
            .order_by(DBWeeklyTeamStats.week)
            .all()
        )
        
        if not weekly_stats:
            return {
                'team_id': team_id,
                'team_name': team.name,
                'weeks': [],
                'player_data': {},
                'team_results': [],
                'mvps': []
            }
        
        # Get all unique players who played any week
        player_ids = set()
        for ws in weekly_stats:
            for ps in ws.player_stats:
                if ps.actual_points > 0:  # Only include players who scored
                    player_ids.add(ps.player_id)
        
        # Build player data structure with cumulative points
        player_data = {}
        for player_id in player_ids:
            player = self.db.query(DBPlayer).filter(DBPlayer.id == player_id).first()
            if player:
                player_data[player_id] = {
                    'name': player.name,
                    'position': player.position,
                    'weeks': [],
                    'cumulative_points': [],
                    'weekly_points': []
                }
        
        # Aggregate data by week
        weeks = []
        team_results = []
        mvps = []
        
        for ws in weekly_stats:
            weeks.append(ws.week)
            
            # Team result for this week
            team_results.append({
                'week': ws.week,
                'result': ws.result,  # W, L, T, U
                'points_for': ws.points_for,
                'points_against': ws.points_against,
                'opponent': ws.opponent_name
            })
            
            # Calculate cumulative points for each player
            week_mvp = None
            week_mvp_points = 0
            
            for player_id in player_ids:
                # Find this player's stats for this week
                player_stat = next(
                    (ps for ps in ws.player_stats if ps.player_id == player_id),
                    None
                )
                
                weekly_points = player_stat.actual_points if player_stat else 0
                
                # Calculate cumulative
                prev_cumulative = (
                    player_data[player_id]['cumulative_points'][-1]
                    if player_data[player_id]['cumulative_points']
                    else 0
                )
                
                cumulative = prev_cumulative + weekly_points
                
                player_data[player_id]['weeks'].append(ws.week)
                player_data[player_id]['weekly_points'].append(weekly_points)
                player_data[player_id]['cumulative_points'].append(cumulative)
                
                # Track MVP for this week (only starters)
                if player_stat and player_stat.slot_position not in ['BE', 'IR']:
                    if weekly_points > week_mvp_points:
                        week_mvp_points = weekly_points
                        week_mvp = player_data[player_id]['name']
            
            mvps.append({
                'week': ws.week,
                'player': week_mvp if week_mvp else 'N/A',
                'points': week_mvp_points
            })
        
        return {
            'team_id': team_id,
            'team_name': team.name,
            'weeks': weeks,
            'player_data': player_data,
            'team_results': team_results,
            'mvps': mvps
        }
    
    def generate_static_visualization(self, team_id: int) -> Optional[str]:
        """
        Generate a static visualization showing final season standings.
        
        Args:
            team_id: Team ID to visualize
            
        Returns:
            Base64 encoded PNG image or None if no data
        """
        data = self.get_season_data(team_id)
        if not data or not data['weeks']:
            return None
        
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 10))
        fig.suptitle(f"{data['team_name']} - Season Overview", fontsize=16, fontweight='bold')
        
        # Top plot: Cumulative points by player
        ax1.set_title('Cumulative Points by Player', fontsize=12, fontweight='bold')
        ax1.set_xlabel('Week')
        ax1.set_ylabel('Cumulative Points')
        ax1.grid(True, alpha=0.3)
        
        # Sort players by final cumulative points and show top performers
        sorted_players = sorted(
            data['player_data'].items(),
            key=lambda x: x[1]['cumulative_points'][-1] if x[1]['cumulative_points'] else 0,
            reverse=True
        )[:10]  # Top 10 players
        
        for player_id, player_info in sorted_players:
            ax1.plot(
                player_info['weeks'],
                player_info['cumulative_points'],
                marker='o',
                label=f"{player_info['name']} ({player_info['position']})",
                linewidth=2
            )
        
        ax1.legend(loc='upper left', fontsize=8)
        
        # Bottom plot: Win/Loss by week
        ax2.set_title('Team Results by Week', fontsize=12, fontweight='bold')
        ax2.set_xlabel('Week')
        ax2.set_ylabel('Result')
        ax2.set_ylim(-0.5, 1.5)
        ax2.set_yticks([0, 1])
        ax2.set_yticklabels(['Loss', 'Win'])
        ax2.grid(True, alpha=0.3)
        
        wins = []
        losses = []
        for result in data['team_results']:
            if result['result'] == 'W':
                wins.append(result['week'])
            elif result['result'] == 'L':
                losses.append(result['week'])
        
        if wins:
            ax2.scatter(wins, [1] * len(wins), color='green', s=200, marker='o', 
                       label='Win', alpha=0.7, edgecolors='black', linewidth=2)
        if losses:
            ax2.scatter(losses, [0] * len(losses), color='red', s=200, marker='o',
                       label='Loss', alpha=0.7, edgecolors='black', linewidth=2)
        
        ax2.legend(loc='upper left')
        
        # Convert plot to base64 image
        buf = io.BytesIO()
        plt.tight_layout()
        plt.savefig(buf, format='png', dpi=100, bbox_inches='tight')
        buf.seek(0)
        img_base64 = base64.b64encode(buf.read()).decode('utf-8')
        plt.close(fig)
        
        return img_base64
    
    def generate_animation_frames(self, team_id: int, max_weeks: Optional[int] = None) -> List[str]:
        """
        Generate a sequence of frames for animation.
        
        Args:
            team_id: Team ID to visualize
            max_weeks: Maximum number of weeks to include (None for all)
            
        Returns:
            List of base64 encoded PNG images, one per week
        """
        data = self.get_season_data(team_id)
        if not data or not data['weeks']:
            return []
        
        weeks = data['weeks'][:max_weeks] if max_weeks else data['weeks']
        frames = []
        
        # Get top players to display
        sorted_players = sorted(
            data['player_data'].items(),
            key=lambda x: x[1]['cumulative_points'][-1] if x[1]['cumulative_points'] else 0,
            reverse=True
        )[:8]  # Top 8 players for animation
        
        for week_idx, week in enumerate(weeks):
            fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 10))
            fig.suptitle(
                f"{data['team_name']} - Week {week} Update",
                fontsize=16,
                fontweight='bold'
            )
            
            # Top plot: Cumulative points up to this week
            ax1.set_title('Cumulative Points by Player', fontsize=12, fontweight='bold')
            ax1.set_xlabel('Week')
            ax1.set_ylabel('Cumulative Points')
            ax1.grid(True, alpha=0.3)
            ax1.set_xlim(min(weeks) - 0.5, max(weeks) + 0.5)
            
            # Find max points for y-axis scaling
            max_points = 0
            for player_id, player_info in sorted_players:
                if player_info['cumulative_points']:
                    max_points = max(max_points, player_info['cumulative_points'][-1])
            ax1.set_ylim(0, max_points * 1.1)
            
            # Plot each player up to current week
            for player_id, player_info in sorted_players:
                weeks_so_far = player_info['weeks'][:week_idx + 1]
                points_so_far = player_info['cumulative_points'][:week_idx + 1]
                
                if weeks_so_far and points_so_far:
                    ax1.plot(
                        weeks_so_far,
                        points_so_far,
                        marker='o',
                        label=f"{player_info['name']} ({player_info['position']})",
                        linewidth=2,
                        markersize=6
                    )
            
            ax1.legend(loc='upper left', fontsize=8)
            
            # Bottom plot: Team results with MVP highlight
            ax2.set_title(
                f"Week {week} Result: {data['team_results'][week_idx]['result']} "
                f"({data['team_results'][week_idx]['points_for']:.1f} - "
                f"{data['team_results'][week_idx]['points_against']:.1f})",
                fontsize=12,
                fontweight='bold'
            )
            
            # Display MVP for this week
            mvp_info = data['mvps'][week_idx]
            ax2.text(
                0.5, 0.6,
                f"Week {week} MVP",
                ha='center',
                fontsize=16,
                fontweight='bold',
                transform=ax2.transAxes
            )
            ax2.text(
                0.5, 0.4,
                f"{mvp_info['player']}",
                ha='center',
                fontsize=20,
                color='gold',
                fontweight='bold',
                transform=ax2.transAxes
            )
            ax2.text(
                0.5, 0.2,
                f"{mvp_info['points']:.2f} points",
                ha='center',
                fontsize=14,
                transform=ax2.transAxes
            )
            ax2.axis('off')
            
            # Add win/loss indicator
            result_color = 'green' if data['team_results'][week_idx]['result'] == 'W' else 'red'
            rect = Rectangle((0.02, 0.02), 0.96, 0.96, transform=ax2.transAxes,
                           facecolor=result_color, alpha=0.1, edgecolor=result_color, linewidth=3)
            ax2.add_patch(rect)
            
            # Convert to base64
            buf = io.BytesIO()
            plt.tight_layout()
            plt.savefig(buf, format='png', dpi=100, bbox_inches='tight')
            buf.seek(0)
            img_base64 = base64.b64encode(buf.read()).decode('utf-8')
            plt.close(fig)
            
            frames.append(img_base64)
        
        return frames
    
    def get_season_summary(self, team_id: int) -> Dict[str, Any]:
        """
        Get summary statistics for the season.
        
        Args:
            team_id: Team ID
            
        Returns:
            Dictionary with summary stats
        """
        data = self.get_season_data(team_id)
        if not data or not data['weeks']:
            return {}
        
        # Calculate wins/losses
        wins = sum(1 for r in data['team_results'] if r['result'] == 'W')
        losses = sum(1 for r in data['team_results'] if r['result'] == 'L')
        
        # Total points
        total_points = sum(r['points_for'] for r in data['team_results'])
        
        # Find season MVP (player with most total points)
        season_mvp = None
        season_mvp_points = 0
        for player_id, player_info in data['player_data'].items():
            if player_info['cumulative_points']:
                final_points = player_info['cumulative_points'][-1]
                if final_points > season_mvp_points:
                    season_mvp_points = final_points
                    season_mvp = player_info['name']
        
        # Count weekly MVPs
        weekly_mvp_counts = {}
        for mvp in data['mvps']:
            player = mvp['player']
            if player != 'N/A':
                weekly_mvp_counts[player] = weekly_mvp_counts.get(player, 0) + 1
        
        return {
            'team_name': data['team_name'],
            'record': f"{wins}-{losses}",
            'total_weeks': len(data['weeks']),
            'total_points': total_points,
            'avg_points_per_week': total_points / len(data['weeks']) if data['weeks'] else 0,
            'season_mvp': season_mvp,
            'season_mvp_points': season_mvp_points,
            'weekly_mvp_counts': weekly_mvp_counts
        }
