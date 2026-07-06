#!/usr/bin/env python3
"""
Scrape Brewers minor league affiliate player-season data from Baseball-Reference.

Outputs:
- data/all_batting_seasons.csv
- data/all_pitching_seasons.csv

Run all years:
    python scripts/brewers_bref_history.py

Run one year for testing:
    python scripts/brewers_bref_history.py --year 2026
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from dataclasses import dataclass
from io import StringIO
from pathlib import Path
from typing import Iterable

import pandas as pd
import requests
from bs4 import BeautifulSoup, Comment


ORG_ID = "MIL"
START_YEAR = 1969
END_YEAR = 2026
BASE_URL = "https://www.baseball-reference.com"
DATA_DIR = Path(__file__).resolve().parents[1] / "data"

# Be polite. Baseball-Reference can block rapid scraping.
REQUEST_SLEEP_SECONDS = 6

# Qualification settings
BATTER_PA_PER_TEAM_GAME = 3.1
PITCHER_IP_PER_TEAM_GAME = 1.0


@dataclass(frozen=True)
class TeamLink:
    year: int
    affiliate: str
    url: str
    team_id: str


def clean_text(value: object) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def numeric(value: object):
    """Convert a value to number when possible, preserving blanks."""
    if value is None:
        return pd.NA
    s = clean_text(value)
    if s == "" or s.lower() in {"nan", "none"}:
        return pd.NA
    s = s.replace(",", "")
    try:
        if "." in s:
            return float(s)
        return int(s)
    except ValueError:
        return pd.NA


def innings_to_float(value: object):
    """
    Baseball innings can be shown as 12.1 or 12.2, meaning 12 1/3 or 12 2/3.
    Convert that notation into true decimal innings for qualification filters.
    """
    s = clean_text(value)
    if not s or s.lower() == "nan":
        return pd.NA

    s = s.replace(",", "")
    if "." not in s:
        try:
            return float(s)
        except ValueError:
            return pd.NA

    whole, frac = s.split(".", 1)
    try:
        whole_int = int(whole)
    except ValueError:
        return pd.NA

    if frac == "1":
        return whole_int + (1 / 3)
    if frac == "2":
        return whole_int + (2 / 3)

    try:
        return float(s)
    except ValueError:
        return pd.NA


def get(url: str) -> str:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (compatible; BrewersBackfieldDataBot/1.0; "
            "+https://github.com/)"
        )
    }
    response = requests.get(url, headers=headers, timeout=45)
    response.raise_for_status()
    return response.text


def soup_from_url(url: str) -> BeautifulSoup:
    html = get(url)
    time.sleep(REQUEST_SLEEP_SECONDS)
    return BeautifulSoup(html, "lxml")


def extract_team_id(url: str) -> str:
    match = re.search(r"id=([^&]+)", url)
    return match.group(1) if match else ""


def extract_team_links(year: int) -> list[TeamLink]:
    affiliate_url = f"{BASE_URL}/register/affiliate.cgi?id={ORG_ID}&year={year}"
    soup = soup_from_url(affiliate_url)

    links: list[TeamLink] = []
    seen: set[str] = set()

    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/register/team.cgi?id=" not in href:
            continue

        full_url = href if href.startswith("http") else BASE_URL + href
        team_id = extract_team_id(full_url)

        if team_id in seen:
            continue

        name = clean_text(a.get_text(" ", strip=True))
        if not name:
            continue

        seen.add(team_id)
        links.append(TeamLink(year=year, affiliate=name, url=full_url, team_id=team_id))

    return links


def html_with_commented_tables(soup: BeautifulSoup) -> str:
    """
    Baseball-Reference often wraps tables in HTML comments.
    pandas.read_html cannot see them unless comments are added back.
    """
    pieces = [str(soup)]
    for comment in soup.find_all(string=lambda text: isinstance(text, Comment)):
        c = str(comment)
        if "<table" in c:
            pieces.append(c)
    return "\n".join(pieces)


def flatten_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [
            clean_text(" ".join([str(x) for x in col if str(x) != "nan"])).split()[-1]
            for col in df.columns
        ]
    else:
        df.columns = [clean_text(c) for c in df.columns]

    # Drop repeated header rows embedded in table body.
    if "Rk" in df.columns:
        df = df[df["Rk"].astype(str) != "Rk"]

    return df.reset_index(drop=True)


def parse_team_meta(soup: BeautifulSoup, team: TeamLink) -> dict[str, object]:
    text = soup.get_text("\n", strip=True)

    level = ""
    league = ""
    games = pd.NA

    # Common page text includes patterns like:
    # "2026 Nashville Sounds Statistics"
    # League/level formatting varies historically, so this is best-effort.
    # The raw tables remain the source of truth for stats.
    possible_level_patterns = [
        r"Level:\s*([A-Za-z0-9+\- ]+)",
        r"Classification:\s*([A-Za-z0-9+\- ]+)",
    ]
    for pattern in possible_level_patterns:
        m = re.search(pattern, text)
        if m:
            level = clean_text(m.group(1))
            break

    m = re.search(r"League:\s*([A-Za-z0-9 .'\-&]+)", text)
    if m:
        league = clean_text(m.group(1))

    # Team games may appear in multiple places. Use the largest plausible game count.
    candidates = []
    for m in re.finditer(r"\b(\d{2,3})\s+G\b", text):
        val = int(m.group(1))
        if 20 <= val <= 180:
            candidates.append(val)
    if candidates:
        games = max(candidates)

    return {
        "Year": team.year,
        "Org": ORG_ID,
        "Affiliate": team.affiliate,
        "Level": level,
        "League": league,
        "Team_ID": team.team_id,
        "Team_URL": team.url,
        "Team_Games": games,
    }


def classify_table(df: pd.DataFrame) -> str | None:
    cols = set(df.columns)

    player_cols = {"Name", "Player"}
    has_player = bool(cols.intersection(player_cols))

    if not has_player:
        return None

    # Batting tables usually have AB/H/HR/RBI or PA/OPS.
    batting_hits = len(cols.intersection({"PA", "AB", "R", "H", "2B", "3B", "HR", "RBI", "SB", "OPS", "BA", "OBP", "SLG"}))

    # Pitching tables usually have IP/ERA/W/L/SO/BB.
    pitching_hits = len(cols.intersection({"IP", "ERA", "W", "L", "SO", "BB", "WHIP", "SV", "GS", "G"}))

    # Avoid confusing batting SO with pitching SO by requiring IP/ERA for pitching.
    if "IP" in cols and ("ERA" in cols or "WHIP" in cols) and pitching_hits >= 4:
        return "pitching"

    if ("AB" in cols or "PA" in cols) and batting_hits >= 5:
        return "batting"

    return None


def normalize_player_column(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    if "Player" not in df.columns and "Name" in df.columns:
        df = df.rename(columns={"Name": "Player"})
    if "Player" in df.columns:
        df["Player"] = df["Player"].map(clean_text)
        df = df[df["Player"] != ""]
        # Remove total/team aggregate rows.
        df = df[~df["Player"].str.lower().isin({"team totals", "league average", "players"})]
    return df


def add_common_columns(df: pd.DataFrame, meta: dict[str, object], source: str) -> pd.DataFrame:
    df = df.copy()
    for key, val in reversed(list(meta.items())):
        df.insert(0, key, val)
    df["Source"] = source
    return df


def add_batting_helpers(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    if "PA" in df.columns:
        df["PA_num"] = df["PA"].map(numeric)
    elif "AB" in df.columns:
        # PA can be missing on older pages. This approximation is better than blank
        # for filtering, but the original AB/BB/HBP/SH/SF columns remain available.
        ab = df["AB"].map(numeric) if "AB" in df.columns else 0
        bb = df["BB"].map(numeric) if "BB" in df.columns else 0
        hbp = df["HBP"].map(numeric) if "HBP" in df.columns else 0
        sh = df["SH"].map(numeric) if "SH" in df.columns else 0
        sf = df["SF"].map(numeric) if "SF" in df.columns else 0
        df["PA_num"] = ab.fillna(0) + bb.fillna(0) + hbp.fillna(0) + sh.fillna(0) + sf.fillna(0)
    else:
        df["PA_num"] = pd.NA

    df["G_num"] = df["G"].map(numeric) if "G" in df.columns else pd.NA

    def is_qualified(row):
        try:
            team_games = float(row.get("Team_Games"))
            pa = float(row.get("PA_num"))
            return pa >= (team_games * BATTER_PA_PER_TEAM_GAME)
        except Exception:
            return False

    df["Qualified_Batter"] = df.apply(is_qualified, axis=1)
    return df


def add_pitching_helpers(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    df["IP_num"] = df["IP"].map(innings_to_float) if "IP" in df.columns else pd.NA
    df["G_num"] = df["G"].map(numeric) if "G" in df.columns else pd.NA

    def is_qualified(row):
        try:
            team_games = float(row.get("Team_Games"))
            ip = float(row.get("IP_num"))
            return ip >= (team_games * PITCHER_IP_PER_TEAM_GAME)
        except Exception:
            return False

    df["Qualified_Pitcher"] = df.apply(is_qualified, axis=1)
    return df


def extract_team_tables(team: TeamLink) -> tuple[list[pd.DataFrame], list[pd.DataFrame]]:
    soup = soup_from_url(team.url)
    meta = parse_team_meta(soup, team)
    html = html_with_commented_tables(soup)

    try:
        tables = pd.read_html(StringIO(html))
    except ValueError:
        return [], []

    batting_tables: list[pd.DataFrame] = []
    pitching_tables: list[pd.DataFrame] = []

    for raw in tables:
        df = flatten_columns(raw)
        table_type = classify_table(df)
        if table_type is None:
            continue

        df = normalize_player_column(df)
        if "Player" not in df.columns or df.empty:
            continue

        df = add_common_columns(df, meta, team.url)

        if table_type == "batting":
            df = add_batting_helpers(df)
            batting_tables.append(df)

        elif table_type == "pitching":
            df = add_pitching_helpers(df)
            pitching_tables.append(df)

    return batting_tables, pitching_tables


def dedupe_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Handle duplicate column names produced by historical tables."""
    cols = []
    seen = {}
    for c in df.columns:
        if c not in seen:
            seen[c] = 0
            cols.append(c)
        else:
            seen[c] += 1
            cols.append(f"{c}_{seen[c]}")
    df = df.copy()
    df.columns = cols
    return df


def scrape_years(years: Iterable[int]) -> tuple[pd.DataFrame, pd.DataFrame]:
    all_batting: list[pd.DataFrame] = []
    all_pitching: list[pd.DataFrame] = []

    for year in years:
        print(f"Finding Brewers affiliates for {year}...", flush=True)
        try:
            teams = extract_team_links(year)
        except Exception as exc:
            print(f"  ERROR loading affiliate page for {year}: {exc}", flush=True)
            continue

        print(f"  Found {len(teams)} affiliate links", flush=True)

        for team in teams:
            print(f"    Scraping {team.affiliate} ({team.team_id})", flush=True)
            try:
                batting, pitching = extract_team_tables(team)
                all_batting.extend(batting)
                all_pitching.extend(pitching)
                print(f"      batting tables: {len(batting)}, pitching tables: {len(pitching)}", flush=True)
            except Exception as exc:
                print(f"      ERROR scraping {team.url}: {exc}", flush=True)
                continue

    batting_df = pd.concat(all_batting, ignore_index=True, sort=False) if all_batting else pd.DataFrame()
    pitching_df = pd.concat(all_pitching, ignore_index=True, sort=False) if all_pitching else pd.DataFrame()

    if not batting_df.empty:
        batting_df = dedupe_columns(batting_df)
        preferred = [
            "Year", "Org", "Affiliate", "Level", "League", "Team_ID", "Team_URL",
            "Team_Games", "Player", "Age", "G", "G_num", "PA", "PA_num",
            "Qualified_Batter", "AB", "R", "H", "2B", "3B", "HR", "RBI",
            "SB", "CS", "BB", "SO", "BA", "OBP", "SLG", "OPS", "Source"
        ]
        batting_df = batting_df[[c for c in preferred if c in batting_df.columns] +
                                [c for c in batting_df.columns if c not in preferred]]

    if not pitching_df.empty:
        pitching_df = dedupe_columns(pitching_df)
        preferred = [
            "Year", "Org", "Affiliate", "Level", "League", "Team_ID", "Team_URL",
            "Team_Games", "Player", "Age", "W", "L", "W-L%", "ERA", "G",
            "G_num", "GS", "GF", "CG", "SHO", "SV", "IP", "IP_num",
            "Qualified_Pitcher", "H", "R", "ER", "HR", "BB", "SO",
            "WHIP", "Source"
        ]
        pitching_df = pitching_df[[c for c in preferred if c in pitching_df.columns] +
                                  [c for c in pitching_df.columns if c not in preferred]]

    return batting_df, pitching_df


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--year", type=int, help="Scrape one year only, useful for testing.")
    args = parser.parse_args()

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    if args.year:
        years = [args.year]
    else:
        years = list(range(START_YEAR, END_YEAR + 1))

    batting_df, pitching_df = scrape_years(years)

    batting_path = DATA_DIR / "all_batting_seasons.csv"
    pitching_path = DATA_DIR / "all_pitching_seasons.csv"

    batting_df.to_csv(batting_path, index=False)
    pitching_df.to_csv(pitching_path, index=False)

    print(f"Wrote {batting_path} ({len(batting_df):,} rows)", flush=True)
    print(f"Wrote {pitching_path} ({len(pitching_df):,} rows)", flush=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
