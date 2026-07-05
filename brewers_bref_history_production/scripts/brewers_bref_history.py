"""
Build a Brewers minor-league affiliate history dataset from Baseball-Reference.

Outputs:
  data/all_batting_seasons.csv
  data/all_pitching_seasons.csv
  data/run_log.csv

Designed to run in GitHub Actions. It does not require Excel.
"""
from __future__ import annotations

import argparse
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

import pandas as pd
import requests
import yaml
from bs4 import BeautifulSoup, Comment

BASE = "https://www.baseball-reference.com"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

BAT_REQUIRED = {"Name", "G"}
BAT_HINTS = {"PA", "AB", "HR", "RBI", "SB", "OPS", "BA"}
PITCH_REQUIRED = {"Name", "G"}
PITCH_HINTS = {"IP", "ERA", "SO", "WHIP", "W", "SV", "BB"}

LEVEL_MAP = {
    "AAA": "AAA",
    "AA": "AA",
    "A+": "A+",
    "A": "A",
    "A-": "A-",
    "Rk": "Rookie",
    "Rookie": "Rookie",
    "FRk": "Rookie",
    "DSL": "DSL",
    "Fgn": "Foreign",
    "Ind": "Independent",
}

@dataclass
class Config:
    org_id: str
    start_year: int
    end_year: int
    request_delay_seconds: float
    season_delay_seconds: float
    output_dir: Path
    qualified_batter_pa_per_game: float
    qualified_pitcher_ip_per_game: float


def load_config(path: Path) -> Config:
    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return Config(
        org_id=str(raw.get("org_id", "MIL")),
        start_year=int(raw.get("start_year", 1969)),
        end_year=int(raw.get("end_year", 2026)),
        request_delay_seconds=float(raw.get("request_delay_seconds", 3)),
        season_delay_seconds=float(raw.get("season_delay_seconds", 5)),
        output_dir=Path(raw.get("output_dir", "data")),
        qualified_batter_pa_per_game=float(raw.get("qualified_batter_pa_per_game", 3.1)),
        qualified_pitcher_ip_per_game=float(raw.get("qualified_pitcher_ip_per_game", 1.0)),
    )


def fetch(url: str, timeout: int = 30) -> BeautifulSoup:
    response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout)
    response.raise_for_status()
    return BeautifulSoup(response.text, "html.parser")


def full_html_with_comments(soup: BeautifulSoup) -> str:
    comments = soup.find_all(string=lambda text: isinstance(text, Comment))
    return str(soup) + "\n" + "\n".join(str(c) for c in comments)


def clean_columns(df: pd.DataFrame) -> pd.DataFrame:
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [str(c[-1]) for c in df.columns]
    else:
        df.columns = [str(c) for c in df.columns]
    df.columns = [c.strip().replace("Unnamed: ", "") for c in df.columns]
    return df.loc[:, ~df.columns.duplicated()].copy()


def numeric_series(series: pd.Series) -> pd.Series:
    return pd.to_numeric(
        series.astype(str)
        .str.replace(",", "", regex=False)
        .str.replace("%", "", regex=False)
        .str.replace("---", "", regex=False)
        .str.strip(),
        errors="coerce",
    )


def ip_to_float(value) -> Optional[float]:
    if pd.isna(value):
        return None
    s = str(value).strip()
    if not s or s.lower() == "nan":
        return None
    # Baseball innings use .1 and .2 as thirds, not tenths.
    if "." in s:
        whole, frac = s.split(".", 1)
        try:
            return int(whole) + {"0": 0.0, "1": 1/3, "2": 2/3}.get(frac[:1], float("nan"))
        except ValueError:
            return None
    try:
        return float(s)
    except ValueError:
        return None


def normalize_player_name(name: str) -> str:
    return re.sub(r"\s+", " ", str(name).replace("*", "").replace("#", "")).strip()


def extract_team_links(affiliate_soup: BeautifulSoup) -> list[dict]:
    rows = []
    seen = set()
    for a in affiliate_soup.find_all("a", href=True):
        href = a["href"]
        if "/register/team.cgi?id=" not in href:
            continue
        url = href if href.startswith("http") else BASE + href
        if url in seen:
            continue
        seen.add(url)
        team_name = a.get_text(" ", strip=True)
        tr = a.find_parent("tr")
        level = ""
        league = ""
        if tr:
            cells = [td.get_text(" ", strip=True) for td in tr.find_all(["th", "td"])]
            # B-Ref affiliate tables vary by era; these guesses are only helpers.
            for cell in cells:
                if cell in LEVEL_MAP:
                    level = LEVEL_MAP.get(cell, cell)
                elif re.fullmatch(r"(AAA|AA|A\+|A|A-|Rk|Rookie|DSL)", cell):
                    level = LEVEL_MAP.get(cell, cell)
            if len(cells) >= 3:
                league = cells[-1] if "League" not in cells[-1] else ""
        rows.append({"Affiliate": team_name, "Level": level, "League": league, "Team_URL": url})
    return rows


def classify_table(df: pd.DataFrame) -> Optional[str]:
    cols = set(df.columns)
    if BAT_REQUIRED.issubset(cols) and len(cols & BAT_HINTS) >= 3:
        return "batting"
    if PITCH_REQUIRED.issubset(cols) and len(cols & PITCH_HINTS) >= 3 and "IP" in cols:
        return "pitching"
    return None


def add_common_fields(df: pd.DataFrame, year: int, team_meta: dict) -> pd.DataFrame:
    df = df.copy()
    df.insert(0, "Year", year)
    df.insert(1, "Affiliate", team_meta.get("Affiliate", ""))
    df.insert(2, "Level", team_meta.get("Level", ""))
    df.insert(3, "League", team_meta.get("League", ""))
    df.insert(4, "Team_URL", team_meta.get("Team_URL", ""))
    if "Name" in df.columns:
        df["Player"] = df["Name"].map(normalize_player_name)
        # Move Player near front and keep original Name later.
        cols = ["Year", "Affiliate", "Level", "League", "Team_URL", "Player"] + [c for c in df.columns if c not in {"Year", "Affiliate", "Level", "League", "Team_URL", "Player"}]
        df = df[cols]
    return df


def finalize_batting(df: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    if df.empty:
        return df
    for col in df.columns:
        if col not in {"Player", "Name", "Affiliate", "Level", "League", "Team_URL"}:
            converted = numeric_series(df[col])
            if converted.notna().sum() > 0:
                df[col + "_num"] = converted
    if "PA" in df.columns:
        df["PA_num"] = numeric_series(df["PA"])
    elif "AB" in df.columns:
        df["PA_num"] = numeric_series(df["AB"])
    else:
        df["PA_num"] = pd.NA
    if "G" in df.columns:
        df["Team_Games_Est"] = df.groupby(["Year", "Affiliate"])["G"].transform(lambda s: numeric_series(s).max())
    else:
        df["Team_Games_Est"] = pd.NA
    df["Qualified_Batter"] = df["PA_num"] >= (df["Team_Games_Est"] * cfg.qualified_batter_pa_per_game)
    return df


def finalize_pitching(df: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    if df.empty:
        return df
    for col in df.columns:
        if col not in {"Player", "Name", "Affiliate", "Level", "League", "Team_URL", "IP"}:
            converted = numeric_series(df[col])
            if converted.notna().sum() > 0:
                df[col + "_num"] = converted
    if "IP" in df.columns:
        df["IP_num"] = df["IP"].map(ip_to_float)
    else:
        df["IP_num"] = pd.NA
    if "G" in df.columns:
        df["Team_Games_Est"] = df.groupby(["Year", "Affiliate"])["G"].transform(lambda s: numeric_series(s).max())
    else:
        df["Team_Games_Est"] = pd.NA
    df["Qualified_Pitcher"] = df["IP_num"] >= (df["Team_Games_Est"] * cfg.qualified_pitcher_ip_per_game)
    return df


def run(cfg: Config, only_year: Optional[int] = None) -> None:
    cfg.output_dir.mkdir(parents=True, exist_ok=True)
    batting_frames = []
    pitching_frames = []
    log_rows = []

    years: Iterable[int] = [only_year] if only_year else range(cfg.start_year, cfg.end_year + 1)

    for year in years:
        affiliate_url = f"{BASE}/register/affiliate.cgi?id={cfg.org_id}&year={year}"
        print(f"Year {year}: {affiliate_url}")
        try:
            affiliate_soup = fetch(affiliate_url)
            teams = extract_team_links(affiliate_soup)
            log_rows.append({"Year": year, "Step": "affiliate_page", "Status": "ok", "Message": f"{len(teams)} teams", "URL": affiliate_url})
        except Exception as exc:
            print(f"  Failed affiliate page: {exc}")
            log_rows.append({"Year": year, "Step": "affiliate_page", "Status": "error", "Message": str(exc), "URL": affiliate_url})
            continue

        for team in teams:
            print(f"  {team['Affiliate']}")
            try:
                soup = fetch(team["Team_URL"])
                tables = pd.read_html(full_html_with_comments(soup))
                added_bat = 0
                added_pitch = 0
                for table in tables:
                    table = clean_columns(table)
                    if "Name" not in table.columns:
                        continue
                    # Drop repeated header rows.
                    table = table[table["Name"].astype(str).str.lower() != "name"].copy()
                    kind = classify_table(table)
                    if kind == "batting":
                        batting_frames.append(add_common_fields(table, year, team))
                        added_bat += 1
                    elif kind == "pitching":
                        pitching_frames.append(add_common_fields(table, year, team))
                        added_pitch += 1
                log_rows.append({"Year": year, "Step": "team_page", "Status": "ok", "Message": f"batting_tables={added_bat}; pitching_tables={added_pitch}", "URL": team["Team_URL"]})
            except Exception as exc:
                print(f"    Failed team page: {exc}")
                log_rows.append({"Year": year, "Step": "team_page", "Status": "error", "Message": str(exc), "URL": team["Team_URL"]})
            time.sleep(cfg.request_delay_seconds)
        time.sleep(cfg.season_delay_seconds)

    batting = pd.concat(batting_frames, ignore_index=True, sort=False) if batting_frames else pd.DataFrame()
    pitching = pd.concat(pitching_frames, ignore_index=True, sort=False) if pitching_frames else pd.DataFrame()

    batting = finalize_batting(batting, cfg)
    pitching = finalize_pitching(pitching, cfg)

    batting.to_csv(cfg.output_dir / "all_batting_seasons.csv", index=False)
    pitching.to_csv(cfg.output_dir / "all_pitching_seasons.csv", index=False)
    pd.DataFrame(log_rows).to_csv(cfg.output_dir / "run_log.csv", index=False)
    print(f"Wrote {len(batting):,} batting rows and {len(pitching):,} pitching rows.")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.yml")
    parser.add_argument("--year", type=int, default=None, help="Optional test mode for one year only")
    args = parser.parse_args()
    cfg = load_config(Path(args.config))
    run(cfg, only_year=args.year)


if __name__ == "__main__":
    main()
