import functions_framework
import firebase_admin
import statsapi
import requests
import logging
import json
import os
from dotenv import load_dotenv
from datetime import date, timedelta
from google.cloud import storage
from firebase_admin import credentials, firestore
from flask import jsonify, make_response

load_dotenv()
api_key = os.getenv("ODDS_API_KEY")
logging.basicConfig(level=logging.INFO)

# Firestore Initialization
try:
    if not firebase_admin._apps:
        env = os.getenv("ENV")
        if env == "LOCAL":
            logging.warning("Initializing Firebase with local credentials")
            cred = credentials.Certificate('service-account/bet-baseball.json')
            firebase_admin.initialize_app(cred)
        else:
            logging.warning("Initializing Firebase with default cloud credentials")
            firebase_admin.initialize_app()

    db = firestore.client()

except Exception as e:
    logging.error(f"Error initializing Firestore: {e}")
    raise

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
        game_id = game["game_id"]
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
                "game_id": game_id,
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

def store_json_in_firestore(data, schedule_date, collection_name="matchups"):
    doc_ref = db.collection(collection_name).document(schedule_date)
    doc_ref.set(data)
    logging.warning(f"Stored matchup data in Firestore at: {collection_name}/{schedule_date}")
    return f"Firestore/{collection_name}/{schedule_date}"

def update_previous_day_document(schedule_date, collection_name="matchups"):
    previous_date = (date.fromisoformat(schedule_date) - timedelta(days=1)).isoformat()
    doc_ref = db.collection(collection_name).document(previous_date)
    doc = doc_ref.get()

    if doc.exists:
        data = doc.to_dict()
        logging.warning(f"Updating previous day's document: {collection_name}/{previous_date}")

        data['past_game'] = True

        for matchup in data.get('matchups', []):
            game_result = statsapi.schedule(game_id=matchup.get('game_id'))[0]
            matchup['winning_team'] = game_result['winning_team']
            favorited_team = matchup['home_team'] if matchup['home_team_rank'] < matchup['away_team_rank'] else matchup['away_team']

            if matchup['winning_team'] == favorited_team:
                matchup['bet_outcome'] = 'W'
            else:
                matchup['bet_outcome'] = 'L'


        doc_ref.set(data)
        logging.warning(f"Successfully updated {collection_name}/{previous_date}")
    else:
        logging.warning(f"No document found for previous date: {previous_date}")



@functions_framework.http
def check_firestore_and_return_response(request):
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

    collection_name = "matchups"
    doc_ref = db.collection(collection_name).document(schedule_date)
    doc = doc_ref.get()

    result = None
    if doc.exists:
        logging.warning(f"Returning cached matchup data from Firestore: {collection_name}/{schedule_date}")
        result = doc.to_dict()
    else:
        logging.warning(f"Generating matchup data for {schedule_date}")
        result = check_matchups(schedule_date, ranking_date, odds_date)

    if request.args.get("store", "false").lower() == "true":
        store_json_in_firestore(result, schedule_date, collection_name)
        update_previous_day_document(schedule_date, collection_name)

    response = make_response(jsonify(result))
    response.headers.update({
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Allow-Headers": "*"
    })
    return response

