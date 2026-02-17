# Additional Data Sources for Fantasy Football Statistics

This document explores additional data sources beyond ESPN that could be integrated into Pigskin Mastermind for importing player and team statistics.

## Summary

Pigskin Mastermind currently supports ESPN Fantasy Football API integration. This document outlines other potential data sources for future integration.

## Currently Implemented

### ESPN Fantasy Football ✅
- **Status**: Fully implemented
- **Access**: Via `espn-api` Python package
- **Data Available**:
  - Current team/player data
  - Historical weekly statistics
  - Live game scores
  - Detailed player stats
- **Authentication**: SWID and ESPN_S2 cookies
- **Documentation**: See [ESPN_API_GUIDE.md](ESPN_API_GUIDE.md)

## Potential Future Data Sources

### 1. Yahoo Fantasy Sports

**Overview**: Yahoo Fantasy Football is another major fantasy platform with a well-documented API.

**Pros**:
- Official API with documentation
- OAuth 2.0 authentication (more secure than cookies)
- Similar data structure to ESPN
- Large user base

**Cons**:
- More complex OAuth setup required
- API rate limits
- Requires app registration with Yahoo

**Available Data**:
- League information
- Team rosters and standings
- Player statistics
- Transaction history
- Draft results

**Implementation Considerations**:
- Use `yahoo-fantasy-api` Python package
- Implement OAuth flow for authentication
- Similar structure to existing `ESPNImporter`

**Resources**:
- [Yahoo Fantasy Sports API Documentation](https://developer.yahoo.com/fantasysports/guide/)
- [yahoo-fantasy-api Python Package](https://pypi.org/project/yahoo-fantasy-api/)

**Estimated Effort**: 2-3 days

### 2. Sleeper Fantasy Football

**Overview**: Sleeper is a modern fantasy platform popular for its user-friendly interface and features.

**Pros**:
- REST API with excellent documentation
- No authentication required for public data
- Modern JSON-based API
- Real-time updates via WebSocket
- Growing user base

**Cons**:
- Smaller market share than ESPN/Yahoo
- Less historical data available
- Some features still in development

**Available Data**:
- League settings and rosters
- Player information and stats
- Draft picks and results
- Waivers and trades
- Trending players

**Implementation Considerations**:
- Direct REST API calls (no wrapper needed)
- Very straightforward integration
- WebSocket support for real-time updates

**Resources**:
- [Sleeper API Documentation](https://docs.sleeper.app/)
- [Sleeper API GitHub](https://github.com/sleeper/docs)

**Estimated Effort**: 1-2 days

### 3. NFL.com Fantasy

**Overview**: Official NFL fantasy platform.

**Pros**:
- Official NFL data
- Large user base
- Comprehensive statistics

**Cons**:
- No official public API
- Would require web scraping or unofficial methods
- More complex authentication
- Terms of service concerns

**Available Data**:
- Similar to ESPN/Yahoo (teams, players, stats)
- Official NFL statistics

**Implementation Considerations**:
- Would need to reverse engineer API or scrape
- Legal/ToS concerns
- More maintenance required

**Estimated Effort**: 3-5 days (risky)

**Recommendation**: ⚠️ Not recommended due to lack of official API

### 4. Pro-Football-Reference.com

**Overview**: Comprehensive NFL statistics database, great for historical data.

**Pros**:
- Extensive historical data
- Detailed player statistics
- Advanced metrics
- Free to access

**Cons**:
- Not a fantasy platform (need to adapt data)
- No real-time fantasy scoring
- Would require web scraping
- Rate limiting needed to be respectful

**Available Data**:
- Player game logs (all seasons)
- Career statistics
- Advanced metrics
- Team statistics
- Historical game data

**Implementation Considerations**:
- Use BeautifulSoup or Scrapy for web scraping
- Cache data aggressively
- Implement respectful rate limiting
- Focus on historical/analytical data, not live fantasy

**Resources**:
- [Pro-Football-Reference](https://www.pro-football-reference.com/)
- Can use existing scraping tools

**Estimated Effort**: 3-4 days

**Use Case**: Historical analysis and projections, not real-time fantasy scoring

### 5. FantasyData.com / SportsData.io

**Overview**: Commercial API providers specializing in sports data.

**Pros**:
- Official, well-maintained APIs
- Comprehensive data coverage
- Reliable uptime
- Good documentation
- Real-time updates

**Cons**:
- **Requires paid subscription** ($$$)
- Overkill for most users
- API keys required

**Available Data**:
- Real-time NFL statistics
- Player projections
- Injury reports
- Depth charts
- Historical data

**Implementation Considerations**:
- Would need to add API key configuration
- Cost consideration for users
- Good for professional/commercial use

**Resources**:
- [FantasyData API](https://fantasydata.com/)
- [SportsData.io](https://sportsdata.io/)

**Estimated Effort**: 2-3 days

**Recommendation**: ⚠️ Only for enterprise/commercial versions

### 6. ESPN Stats API (Public)

**Overview**: ESPN's public statistics API (separate from Fantasy API).

**Pros**:
- No authentication required
- Real-time NFL statistics
- Free to use
- Good for general NFL data

**Cons**:
- Doesn't include fantasy-specific data
- Need to calculate fantasy points manually
- Less detailed than fantasy platforms

**Available Data**:
- Live game scores
- Player statistics
- Team information
- Schedules

**Implementation Considerations**:
- Good supplement to fantasy data
- Can use for real-time score updates
- Need to implement fantasy scoring logic

**Resources**:
- [ESPN API Documentation (unofficial)](https://gist.github.com/akeaswaran/b48b02f1c94f873c6655e7129910fc3b)

**Estimated Effort**: 2-3 days

**Use Case**: Real-time game tracking, not fantasy management

### 7. The Athletic / Fantasy Football Calculators

**Overview**: Fantasy football analytics and projection sites.

**Pros**:
- High-quality projections
- Expert analysis
- Advanced metrics

**Cons**:
- No official APIs
- Would require scraping
- Subscription required for some data
- Legal/ToS concerns

**Recommendation**: ❌ Not recommended

## Recommended Implementation Priority

Based on effort, legality, and value:

### Phase 1: Fantasy Platforms (User Leagues)
1. **Yahoo Fantasy** ⭐⭐⭐⭐⭐
   - Second most popular platform
   - Official API available
   - Similar to ESPN implementation
   - High user value

2. **Sleeper** ⭐⭐⭐⭐
   - Modern, growing platform
   - Excellent API documentation
   - Easy to implement
   - Good for younger demographic

### Phase 2: Statistical Data (Analysis)
3. **ESPN Stats API** ⭐⭐⭐
   - Complements fantasy data
   - Real-time game tracking
   - Free and legal
   - Good for entertainment features

4. **Pro-Football-Reference** ⭐⭐⭐
   - Historical analysis
   - Advanced metrics
   - Projection improvements
   - Requires careful scraping

### Phase 3: Commercial (Optional)
5. **FantasyData / SportsData** ⭐⭐
   - Only if building commercial product
   - Requires paid subscription
   - Most reliable data source

### Not Recommended
- NFL.com Fantasy (no API)
- The Athletic (no API, legal issues)
- Other sites without official APIs

## Implementation Notes

### Common Patterns

All fantasy platform integrations should follow this pattern:

```python
class FantasyServiceImporter(ABC):
    """Abstract base class for all importers"""
    
    @abstractmethod
    def authenticate(self, credentials: Dict[str, str]) -> bool:
        """Authenticate with the service"""
        pass
    
    @abstractmethod
    def import_team(self, team_id: str) -> Team:
        """Import a team"""
        pass
    
    @abstractmethod
    def get_player_data(self, player_id: str) -> Player:
        """Get player data"""
        pass
```

### Authentication Strategies

1. **Cookie-based** (ESPN): Extract from browser
2. **OAuth** (Yahoo): Redirect flow with tokens
3. **API Key** (Commercial APIs): Simple key in headers
4. **Public** (Sleeper, ESPN Stats): No auth needed

### Data Mapping

Different platforms use different data structures. Create mapping functions:

```python
def map_yahoo_player_to_model(yahoo_player) -> Player:
    """Convert Yahoo player format to our Player model"""
    return Player(
        player_id=f"yahoo_{yahoo_player['player_id']}",
        name=yahoo_player['name']['full'],
        position=yahoo_player['position'],
        # ... map other fields
    )
```

### Testing Strategy

1. Use mocks for all external API calls
2. Test authentication failures
3. Test data mapping
4. Test error handling
5. Integration tests with real APIs (optional, in separate test suite)

## Conclusion

The current ESPN integration provides a solid foundation. The recommended next steps are:

1. **Yahoo Fantasy** - Captures another major user base with official API
2. **Sleeper** - Modern platform with excellent API
3. **ESPN Stats API** - Enhances real-time tracking features

Avoid platforms without official APIs due to legal concerns and maintenance burden.

## Contributing

If you'd like to contribute an integration for any of these platforms:

1. Follow the `FantasyServiceImporter` abstract base class
2. Add comprehensive tests
3. Document authentication requirements
4. Update the `ImporterFactory`
5. Add CLI commands
6. Write documentation similar to ESPN_API_GUIDE.md

See [CONTRIBUTING.md](../CONTRIBUTING.md) for more details.
