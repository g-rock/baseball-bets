import functions_framework
import statsapi
import requests
import logging
import json
from dotenv import load_dotenv
import os
from datetime import datetime, timezone, timedelta
from flask import jsonify, make_response

load_dotenv()
api_key = os.getenv("ODDS_API_KEY")

# Set up logging
logging.basicConfig(level=logging.DEBUG)


def get_team_rankings(ranking_date: str):
    """Fetches top 10 and bottom 10 teams based on win percentage for a given date."""
    logging.info(f"Fetching rankings for date: {ranking_date}")
    year, month, day = ranking_date.split('-')
    
    standings = statsapi.standings_data(leagueId='103,104', season=int(year), date=ranking_date)
    
    teams = []

    for division in standings.values():
        for team in division['teams']:
            total_games = team["w"] + team["l"]
            win_pct = float(team["w"]) / total_games if total_games > 0 else 0.0
            
            teams.append({
                "name": team["name"],
                "wins": team["w"],
                "losses": team["l"],
                "win_pct": win_pct
            })

    sorted_teams = sorted(teams, key=lambda x: x["win_pct"], reverse=True)

    # Get top 10 and bottom 10 teams
    top_10 = sorted_teams[:10]
    bottom_10 = sorted_teams[-10:]

    logging.debug(f"Top 10 teams: {json.dumps(top_10, indent=4)}")
    logging.debug(f"Bottom 10 teams: {json.dumps(bottom_10, indent=4)}")

    return {
        "top_10": top_10,
        "bottom_10": bottom_10,
        "all_teams": sorted_teams
    }


def get_all_game_odds(odds_date: str):
    """Fetch current odds for all games on a given date from the-odds-api.com."""
    logging.info(f"Fetching game odds for date: {odds_date}")

    # Set time range for the odds date
    commence_time_from = f"{odds_date}T00:00:00Z"
    commence_time_to = f"{odds_date}T23:59:59Z"

    url = (
        f"https://api.the-odds-api.com/v4/sports/baseball_mlb/odds"
        f"?apiKey={api_key}&regions=us&markets=h2h"
        f"&commenceTimeFrom={commence_time_from}"
        f"&commenceTimeTo={commence_time_to}"
    )

    logging.info(url)
    response = requests.get(url)

    if response.status_code == 200:
        logging.debug(f"Odds data: {json.dumps(response.json())}")
        return response.json()
    else:
        logging.error("Failed to fetch odds data")
        return []

def filter_odds_for_team(odds_data, home_team):
    """Filter the odds data for the matching home team."""
    for game in odds_data:
        if game.get("home_team") == home_team:
            return game.get("bookmakers", [])
    return []

def check_matchups(schedule_date: str, ranking_date: str, odds_date: str):
    """Checks if any top 10 team is playing a bottom 10 team on a given date."""
    logging.info(f"Checking matchups for schedule_date: {schedule_date}, ranking_date: {ranking_date}, odds_date: {odds_date}")

    # Fetch rankings and schedule data
    rankings = get_team_rankings(ranking_date)
    schedule = statsapi.schedule(start_date=schedule_date, end_date=schedule_date)

    matchups = set()
    matchup_details = []

    # Get odds data
    odds_data = get_all_game_odds(odds_date)

    top_10 = rankings["top_10"]
    bottom_10 = rankings["bottom_10"]

    for game in schedule:
        home_team = game["home_name"]
        away_team = game["away_name"]

        if (home_team, away_team) in matchups or (away_team, home_team) in matchups:
            continue
        
        matchups.add((home_team, away_team))

        try:
            home_rank = next((idx + 1 for idx, team in enumerate(rankings["all_teams"]) if team["name"] == home_team), None)
            away_rank = next((idx + 1 for idx, team in enumerate(rankings["all_teams"]) if team["name"] == away_team), None)
        except StopIteration:
            continue

        if home_rank is None or away_rank is None:
            continue

        is_home_top_10 = any(team["name"] == home_team for team in top_10)
        is_away_top_10 = any(team["name"] == away_team for team in top_10)
        is_home_bottom_10 = any(team["name"] == home_team for team in bottom_10)
        is_away_bottom_10 = any(team["name"] == away_team for team in bottom_10)

        if (is_home_top_10 and is_away_bottom_10) or (is_home_bottom_10 and is_away_top_10):
            ranking_diff = abs(home_rank - away_rank)
            matchup_odds = filter_odds_for_team(odds_data, home_team)
            matchup_details.append({
                "home_team": home_team,
                "home_team_rank": home_rank,
                "away_team": away_team,
                "away_team_rank": away_rank,
                "ranking_diff": ranking_diff,
                "game_time": game["game_datetime"],
                "odds": matchup_odds
            })

    matchup_details_sorted = sorted(matchup_details, key=lambda x: x["ranking_diff"], reverse=True)

    return {
        "schedule_date": schedule_date,
        "ranking_date": ranking_date,
        "odds_date": odds_date,
        "matchups": matchup_details_sorted
    }


@functions_framework.http
def get_top_vs_bottom_matchups(request):
    """Cloud Function to return matchups with top 10 vs bottom 10 teams using three different dates."""

    # Handle preflight (OPTIONS) requests for CORS
    if request.method == "OPTIONS":
        response = make_response()
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "*"
        return response

    # Extract date parameters from request
    schedule_date = request.args.get("schedule_date")
    ranking_date = request.args.get("ranking_date")
    odds_date = request.args.get("odds_date")

    if not schedule_date or not ranking_date or not odds_date:
        logging.error("All three date parameters are required")
        response = jsonify({"error": "schedule_date, ranking_date, and odds_date parameters are required"}), 400
    else:
        logging.info(f"Received request for dates - Schedule: {schedule_date}, Ranking: {ranking_date}, Odds: {odds_date}")
        response = jsonify(check_matchups(schedule_date, ranking_date, odds_date))

    # Add CORS headers to the response
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "*"

    return response
