import functions_framework
import statsapi
import requests
import logging
import json
import os
from dotenv import load_dotenv
from datetime import date
from google.cloud import storage
from flask import jsonify, make_response

load_dotenv()
api_key = os.getenv("ODDS_API_KEY")
logging.basicConfig(level=logging.INFO)

def get_team_rankings(ranking_date):
    year, month, day = ranking_date.split('-')
    standings = statsapi.standings_data(leagueId='103,104', season=int(year), date=ranking_date)
    teams = [
        {
            "name": team["name"],
            "wins": team["w"],
            "losses": team["l"],
            "win_pct": float(team["w"]) / (team["w"] + team["l"]) if team["w"] + team["l"] > 0 else 0.0
        }
        for division in standings.values()
        for team in division['teams']
    ]
    sorted_teams = sorted(teams, key=lambda x: x["win_pct"], reverse=True)
    return {
        "top_10": sorted_teams[:10],
        "bottom_10": sorted_teams[-10:],
        "all_teams": sorted_teams
    }

def get_all_game_odds(odds_date):
    url = (
        f"https://api.the-odds-api.com/v4/sports/baseball_mlb/odds"
        f"?apiKey={api_key}&regions=us&markets=h2h"
        f"&commenceTimeFrom={odds_date}T00:00:00Z"
    )
    response = requests.get(url)
    return response.json() if response.status_code == 200 else []

def filter_odds_for_team(odds_data, home_team):
    for game in odds_data:
        if game.get("home_team") == home_team:
            return game.get("bookmakers", [])
    return []

def get_pitcher_stats(pitcher_name):
    pitcher_lookup = statsapi.lookup_player(pitcher_name)
    if pitcher_lookup:
        pitcher_id = pitcher_lookup[0]['id']
        stats_data = statsapi.player_stat_data(pitcher_id, group="[pitching]", type="season")
        if isinstance(stats_data, dict) and 'stats' in stats_data:
            stat_list = stats_data['stats']
            if stat_list:
                stats = stat_list[0].get('stats', {})
                return {
                    "name": pitcher_name,
                    "era": stats.get('era'),
                    "inningsPitched": stats.get('inningsPitched')
                }
        logging.warning(f"No valid season stats for {pitcher_name}")
    else:
        logging.warning(f"Pitcher not found: {pitcher_name}")
    return {
        "name": pitcher_name,
        "era": None,
        "inningsPitched": None
    }

def check_matchups(schedule_date, ranking_date, odds_date):
    rankings = get_team_rankings(ranking_date)
    schedule = statsapi.schedule(start_date=schedule_date, end_date=schedule_date)
    odds_data = get_all_game_odds(odds_date)
    matchups = set()
    matchup_details = []

    for game in schedule:
        home_team, away_team = game["home_name"], game["away_name"]
        home_probable_pitcher, away_probable_pitcher = game["home_probable_pitcher"], game["away_probable_pitcher"]
        home_pitcher_stats = get_pitcher_stats(home_probable_pitcher)
        away_pitcher_stats = get_pitcher_stats(away_probable_pitcher)
        if (home_team, away_team) in matchups or (away_team, home_team) in matchups:
            continue
        matchups.add((home_team, away_team))

        home_rank = next((i+1 for i, team in enumerate(rankings["all_teams"]) if team["name"] == home_team), None)
        away_rank = next((i+1 for i, team in enumerate(rankings["all_teams"]) if team["name"] == away_team), None)
        if home_rank is None or away_rank is None:
            continue

        is_home_top = any(t["name"] == home_team for t in rankings["top_10"])
        is_away_top = any(t["name"] == away_team for t in rankings["top_10"])
        is_home_bottom = any(t["name"] == home_team for t in rankings["bottom_10"])
        is_away_bottom = any(t["name"] == away_team for t in rankings["bottom_10"])

        if (is_home_top and is_away_bottom) or (is_home_bottom and is_away_top):
            matchup_details.append({
                "home_team": home_team,
                "home_team_rank": home_rank,
                "home_pitcher": home_pitcher_stats,
                "away_team": away_team,
                "away_team_rank": away_rank,
                "away_pitcher": away_pitcher_stats,
                "ranking_diff": abs(home_rank - away_rank),
                "game_time": game["game_datetime"],
                "odds": filter_odds_for_team(odds_data, home_team)
            })

    return {
        "schedule_date": schedule_date,
        "ranking_date": ranking_date,
        "odds_date": odds_date,
        "matchups": sorted(matchup_details, key=lambda x: x["ranking_diff"], reverse=True)
    }

def store_json_in_gcs_by_date(data, schedule_date, bucket_name="daily-baseball", folder="matchups"):
    client = storage.Client()
    bucket = client.bucket(bucket_name)
    filename = f"{folder}/matchup_{schedule_date.replace(':', '-')}.json"
    blob = bucket.blob(filename)
    blob.upload_from_string(json.dumps(data, indent=2), content_type="application/json")
    logging.info(f"Uploaded matchup data to GCS at: {filename}")
    return f"gs://{bucket_name}/{filename}"

@functions_framework.http
def check_bucket_and_return_response(request):
    if request.method == "OPTIONS":
        response = make_response()
        response.headers.update({
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
            "Access-Control-Allow-Headers": "*"
        })
        return response

    today = date.today()
    schedule_date = request.args.get("schedule_date") or today.isoformat()
    ranking_date = request.args.get("ranking_date") or today.isoformat()
    odds_date = request.args.get("odds_date") or today.isoformat()

    bucket_name = "daily-baseball"
    folder = "matchups"
    filename = f"{folder}/matchup_{schedule_date}.json"

    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(filename)

    result = None
    if blob.exists():
        logging.info(f"Returning cached matchup data from GCS: {filename}")
        result = json.loads(blob.download_as_text())
    else:
        logging.info(f"Generating matchup data for {schedule_date}")
        result = check_matchups(schedule_date, ranking_date, odds_date)

        if request.args.get("store", "false").lower() == "true":
            store_json_in_gcs_by_date(result, schedule_date, bucket_name, folder)

    response = make_response(jsonify(result))
    response.headers.update({
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Allow-Headers": "*"
    })
    return response

