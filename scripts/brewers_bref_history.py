#!/usr/bin/env python3

from __future__ import annotations

import argparse
import re
import time
from io import StringIO
from pathlib import Path

import pandas as pd
import requests
from bs4 import BeautifulSoup, Comment


ORG_ID = "MIL"
START_YEAR = 1969
END_YEAR = 2026
BASE_URL = "https://www.baseball-reference.com"
DATA_DIR = Path(__file__).resolve().parents[1] / "data"

REQUEST_SLEEP_SECONDS = 6

BATTER_PA_PER_TEAM_GAME = 3.1
PITCHER_IP_PER_TEAM_GAME = 1.0


def clean(value):
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def get_html(url):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    r = requests.get(url, headers=headers, timeout=45)

    if r.status_code != 200:
        raise RuntimeError(f"Request failed: {r.status_code} for {url}")

    time.sleep(REQUEST_SLEEP_SECONDS)
    return r.text


def get_soup(url):
    return BeautifulSoup(get_html(url), "lxml")


def extract_team_id(url):
    m = re.search(r"id=([^&]+)", url)
    return m.group(1) if m else ""


def get_affiliate_team_links(year):
    url = f"{BASE_URL}/register/affiliate.cgi?id={ORG_ID}&year={year}"
    soup = get_soup(url)

    full_html = html_with_comments(soup)
    soup = BeautifulSoup(full_html, "lxml")

    teams = []
    seen = set()

    for a in soup.find_all("a", href=True):
        href = a["href"]

        if "/register/team.cgi?id=" not in href:
            continue

        full_url = href if href.startswith("http") else BASE_URL + href
        team_id = extract_team_id(full_url)
        team_name = clean(a.get_text())

        if not team_id or not team_name or team_id in seen:
            continue

        seen.add(team_id)
        teams.append(
            {
                "Year": year,
                "Affiliate": team_name,
                "Team_ID": team_id,
                "Team_URL": full_url,
            }
        )

    if not teams:
        raise RuntimeError(f"Found 0 affiliate team links for {year}. Affiliate page may be blocked or link pattern changed: {url}")

    return teams
    
def html_with_comments(soup):
    html_parts = [str(soup)]

    for comment in soup.find_all(string=lambda text: isinstance(text, Comment)):
        comment_text = str(comment)
        if "<table" in comment_text:
            html_parts.append(comment_text)

    return "\n".join(html_parts)


def flatten_columns(df):
    df = df.copy()

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [
            clean(" ".join(str(x) for x in col if str(x) != "nan")).split()[-1]
            for col in df.columns
        ]
    else:
        df.columns = [clean(c) for c in df.columns]

    return df


def find_team_meta(soup):
    text = soup.get_text("\n", strip=True)

    level = ""
    league = ""
    games = None

    m = re.search(r"Level:\s*([A-Za-z0-9+\- ]+)", text)
    if m:
        level = clean(m.group(1))

    m = re.search(r"League:\s*([A-Za-z0-9 .'\-&]+)", text)
    if m:
        league = clean(m.group(1))

    game_candidates = []
    for m in re.finditer(r"\b(\d{2,3})\s+G\b", text):
        val = int(m.group(1))
        if 20 <= val <= 180:
            game_candidates.append(val)

    if game_candidates:
        games = max(game_candidates)

    return level, league, games


def to_number(value):
    s = clean(value).replace(",", "")
    if s == "" or s.lower() == "nan":
        return pd.NA

    try:
        if "." in s:
            return float(s)
        return int(s)
    except Exception:
        return pd.NA


def innings_to_float(value):
    s = clean(value).replace(",", "")

    if s == "" or s.lower() == "nan":
        return pd.NA

    if "." not in s:
        try:
            return float(s)
        except Exception:
            return pd.NA

    whole, frac = s.split(".", 1)

    try:
        whole = int(whole)
    except Exception:
        return pd.NA

    if frac == "1":
        return whole + 1 / 3
    if frac == "2":
        return whole + 2 / 3

    try:
        return float(s)
    except Exception:
        return pd.NA


def normalize_player_column(df):
    df = df.copy()

    if "Player" not in df.columns and "Name" in df.columns:
        df = df.rename(columns={"Name": "Player"})

    if "Player" not in df.columns:
        return df

    df["Player"] = df["Player"].map(clean)

    df = df[df["Player"] != ""]
    df = df[df["Player"].str.lower() != "player"]
    df = df[df["Player"].str.lower() != "team totals"]
    df = df[df["Player"].str.lower() != "league average"]

    return df


def classify_table(df):
    cols = set(df.columns)

    if "Player" not in cols and "Name" not in cols:
        return None

    if "IP" in cols and ("ERA" in cols or "WHIP" in cols) and ("SO" in cols or "BB" in cols):
        return "pitching"

    batting_cols = {"PA", "AB", "R", "H", "2B", "3B", "HR", "RBI", "SB", "BB", "SO", "BA", "OBP", "SLG", "OPS"}

    if ("AB" in cols or "PA" in cols) and len(cols.intersection(batting_cols)) >= 5:
        return "batting"

    return None


def add_common_columns(df, team, level, league, games):
    df = df.copy()

    df.insert(0, "Year", team["Year"])
    df.insert(1, "Org", ORG_ID)
    df.insert(2, "Affiliate", team["Affiliate"])
    df.insert(3, "Level", level)
    df.insert(4, "League", league)
    df.insert(5, "Team_Games", games)
    df.insert(6, "Team_ID", team["Team_ID"])
    df.insert(7, "Team_URL", team["Team_URL"])

    return df


def add_batting_helpers(df):
    df = df.copy()

    if "PA" in df.columns:
        df["PA_num"] = df["PA"].map(to_number)
    elif "AB" in df.columns:
        ab = df["AB"].map(to_number) if "AB" in df.columns else 0
        bb = df["BB"].map(to_number) if "BB" in df.columns else 0
        hbp = df["HBP"].map(to_number) if "HBP" in df.columns else 0
        sh = df["SH"].map(to_number) if "SH" in df.columns else 0
        sf = df["SF"].map(to_number) if "SF" in df.columns else 0
        df["PA_num"] = ab.fillna(0) + bb.fillna(0) + hbp.fillna(0) + sh.fillna(0) + sf.fillna(0)
    else:
        df["PA_num"] = pd.NA

    if "G" in df.columns:
        df["G_num"] = df["G"].map(to_number)
    else:
        df["G_num"] = pd.NA

    def qualified(row):
        try:
            return float(row["PA_num"]) >= float(row["Team_Games"]) * BATTER_PA_PER_TEAM_GAME
        except Exception:
            return False

    df["Qualified_Batter"] = df.apply(qualified, axis=1)

    return df


def add_pitching_helpers(df):
    df = df.copy()

    if "IP" in df.columns:
        df["IP_num"] = df["IP"].map(innings_to_float)
    else:
        df["IP_num"] = pd.NA

    if "G" in df.columns:
        df["G_num"] = df["G"].map(to_number)
    else:
        df["G_num"] = pd.NA

    def qualified(row):
        try:
            return float(row["IP_num"]) >= float(row["Team_Games"]) * PITCHER_IP_PER_TEAM_GAME
        except Exception:
            return False

    df["Qualified_Pitcher"] = df.apply(qualified, axis=1)

    return df


def scrape_team(team):
    soup = get_soup(team["Team_URL"])
    level, league, games = find_team_meta(soup)

    full_html = html_with_comments(soup)

    try:
        tables = pd.read_html(StringIO(full_html))
    except ValueError:
        return [], []

    batting_rows = []
    pitching_rows = []

    for table in tables:
        df = flatten_columns(table)
        df = normalize_player_column(df)

        table_type = classify_table(df)

        if table_type is None or df.empty:
            continue

        df = add_common_columns(df, team, level, league, games)

        if table_type == "batting":
            df = add_batting_helpers(df)
            batting_rows.append(df)

        elif table_type == "pitching":
            df = add_pitching_helpers(df)
            pitching_rows.append(df)

    return batting_rows, pitching_rows


def order_columns(df, preferred):
    existing = [c for c in preferred if c in df.columns]
    remaining = [c for c in df.columns if c not in existing]
    return df[existing + remaining]


def scrape(years):
    all_batting = []
    all_pitching = []

    for year in years:
        print(f"Finding affiliates for {year}...", flush=True)

        teams = get_affiliate_team_links(year)

        print(f"Found {len(teams)} teams for {year}", flush=True)

        for team in teams:
            print(f"Scraping {year} {team['Affiliate']}...", flush=True)

            try:
                batting, pitching = scrape_team(team)
                all_batting.extend(batting)
                all_pitching.extend(pitching)
                print(f"  batting tables: {len(batting)} | pitching tables: {len(pitching)}", flush=True)

            except Exception as e:
                print(f"  ERROR scraping {team['Team_URL']}: {e}", flush=True)

    batting_df = pd.concat(all_batting, ignore_index=True, sort=False) if all_batting else pd.DataFrame()
    pitching_df = pd.concat(all_pitching, ignore_index=True, sort=False) if all_pitching else pd.DataFrame()

    if batting_df.empty and pitching_df.empty:
        raise RuntimeError("Scraper collected 0 batting rows and 0 pitching rows. Check Baseball-Reference parsing or blocking.")

    batting_preferred = [
        "Year", "Org", "Affiliate", "Level", "League", "Team_Games", "Team_ID", "Team_URL",
        "Player", "Age", "G", "G_num", "PA", "PA_num", "Qualified_Batter",
        "AB", "R", "H", "2B", "3B", "HR", "RBI", "SB", "CS", "BB", "SO",
        "BA", "OBP", "SLG", "OPS"
    ]

    pitching_preferred = [
        "Year", "Org", "Affiliate", "Level", "League", "Team_Games", "Team_ID", "Team_URL",
        "Player", "Age", "W", "L", "W-L%", "ERA", "G", "G_num", "GS", "GF",
        "CG", "SHO", "SV", "IP", "IP_num", "Qualified_Pitcher",
        "H", "R", "ER", "HR", "BB", "SO", "WHIP"
    ]

    if not batting_df.empty:
        batting_df = order_columns(batting_df, batting_preferred)

    if not pitching_df.empty:
        pitching_df = order_columns(pitching_df, pitching_preferred)

    return batting_df, pitching_df


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--year", type=int, help="Optional single year to scrape, like 2026.")
    args = parser.parse_args()

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    if args.year:
        years = [args.year]
    else:
        years = range(START_YEAR, END_YEAR + 1)

    batting_df, pitching_df = scrape(years)

    batting_path = DATA_DIR / "all_batting_seasons.csv"
    pitching_path = DATA_DIR / "all_pitching_seasons.csv"

    batting_df.to_csv(batting_path, index=False)
    pitching_df.to_csv(pitching_path, index=False)

    print(f"Wrote {batting_path} with {len(batting_df):,} rows", flush=True)
    print(f"Wrote {pitching_path} with {len(pitching_df):,} rows", flush=True)


if __name__ == "__main__":
    main()
