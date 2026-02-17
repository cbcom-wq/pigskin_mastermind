"""Tests for fantasy service importers."""

import pytest
from unittest.mock import Mock, patch
from pigskin_mastermind.services.importer import ESPNImporter, ImporterFactory


@patch('pigskin_mastermind.services.importer.League')
def test_espn_importer_authenticate_success(mock_league_class):
    """Test successful ESPN authentication"""
    # Setup mock
    mock_league = Mock()
    mock_league.teams = []
    mock_league_class.return_value = mock_league
    
    importer = ESPNImporter()
    credentials = {
        'swid': 'test_swid',
        'espn_s2': 'test_s2',
        'league_id': '123456',
        'year': '2024'
    }
    
    result = importer.authenticate(credentials)
    
    assert result is True
    assert importer.authenticated is True
    assert importer.league_id == 123456
    assert importer.year == 2024


@patch('pigskin_mastermind.services.importer.League')
def test_espn_importer_authenticate_failure(mock_league_class):
    """Test failed ESPN authentication"""
    # Setup mock to raise exception
    mock_league_class.side_effect = Exception("Invalid credentials")
    
    importer = ESPNImporter()
    credentials = {
        'swid': 'bad_swid',
        'espn_s2': 'bad_s2',
        'league_id': '123456',
        'year': '2024'
    }
    
    result = importer.authenticate(credentials)
    
    assert result is False
    assert importer.authenticated is False


def test_espn_importer_authenticate_missing_credentials():
    """Test authentication with missing credentials"""
    importer = ESPNImporter()
    credentials = {
        'swid': 'test_swid',
        # Missing espn_s2 and league_id
    }
    
    result = importer.authenticate(credentials)
    
    assert result is False


@patch('pigskin_mastermind.services.importer.League')
def test_espn_importer_import_team(mock_league_class):
    """Test importing a team from ESPN"""
    # Setup mock team
    mock_team = Mock()
    mock_team.team_id = 1
    mock_team.team_name = "Test Team"
    mock_team.owners = [{'displayName': 'Test Owner'}]
    mock_team.wins = 8
    mock_team.losses = 5
    mock_team.ties = 0
    mock_team.points_for = 1234.5
    
    # Setup mock player
    mock_player = Mock()
    mock_player.playerId = 12345
    mock_player.name = "Patrick Mahomes"
    mock_player.position = "QB"
    mock_player.proTeam = "KC"
    mock_player.projected_points = 25.5
    mock_player.points = 22.3
    mock_player.stats = {'passing_yards': 350}
    
    mock_team.roster = [mock_player]
    
    # Setup mock league
    mock_league = Mock()
    mock_league.teams = [mock_team]
    mock_league_class.return_value = mock_league
    
    # Test
    importer = ESPNImporter()
    credentials = {
        'swid': 'test_swid',
        'espn_s2': 'test_s2',
        'league_id': '123456',
        'year': '2024'
    }
    importer.authenticate(credentials)
    
    team = importer.import_team('1')
    
    assert team is not None
    assert team.name == "Test Team"
    assert team.owner == "Test Owner"
    assert team.record['wins'] == 8
    assert team.record['losses'] == 5
    assert team.total_points == 1234.5
    assert len(team.players) == 1
    assert team.players[0].name == "Patrick Mahomes"
    assert team.players[0].position == "QB"


@patch('pigskin_mastermind.services.importer.League')
def test_espn_importer_get_player_data(mock_league_class):
    """Test getting player data from ESPN"""
    # Setup mock player
    mock_player = Mock()
    mock_player.playerId = 12345
    mock_player.name = "Travis Kelce"
    mock_player.position = "TE"
    mock_player.proTeam = "KC"
    mock_player.projected_points = 15.5
    mock_player.points = 18.2
    mock_player.stats = {'receptions': 8, 'receiving_yards': 95}
    
    # Setup mock team
    mock_team = Mock()
    mock_team.roster = [mock_player]
    
    # Setup mock league
    mock_league = Mock()
    mock_league.teams = [mock_team]
    mock_league_class.return_value = mock_league
    
    # Test
    importer = ESPNImporter()
    credentials = {
        'swid': 'test_swid',
        'espn_s2': 'test_s2',
        'league_id': '123456'
    }
    importer.authenticate(credentials)
    
    player = importer.get_player_data('12345')
    
    assert player is not None
    assert player.name == "Travis Kelce"
    assert player.position == "TE"
    assert player.team == "KC"
    assert player.projected_points == 15.5
    assert player.actual_points == 18.2


def test_espn_importer_not_authenticated():
    """Test that importing fails when not authenticated"""
    importer = ESPNImporter()
    
    with pytest.raises(RuntimeError, match="Not authenticated"):
        importer.import_team('1')
    
    with pytest.raises(RuntimeError, match="Not authenticated"):
        importer.get_player_data('12345')


@patch('pigskin_mastermind.services.importer.League')
def test_espn_importer_team_not_found(mock_league_class):
    """Test importing a team that doesn't exist"""
    # Setup mock league with no matching team
    mock_other_team = Mock()
    mock_other_team.team_id = 99
    
    mock_league = Mock()
    mock_league.teams = [mock_other_team]
    mock_league_class.return_value = mock_league
    
    importer = ESPNImporter()
    credentials = {
        'swid': 'test_swid',
        'espn_s2': 'test_s2',
        'league_id': '123456'
    }
    importer.authenticate(credentials)
    
    with pytest.raises(ValueError, match="Team 1 not found"):
        importer.import_team('1')


def test_importer_factory_espn():
    """Test factory creates ESPN importer"""
    importer = ImporterFactory.create_importer('espn')
    assert isinstance(importer, ESPNImporter)


def test_importer_factory_unsupported():
    """Test factory raises error for unsupported service"""
    with pytest.raises(ValueError, match="Unsupported fantasy service"):
        ImporterFactory.create_importer('unsupported')
