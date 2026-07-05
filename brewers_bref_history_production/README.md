# Brewers Baseball-Reference Minor League History

This repo builds a database of Brewers minor-league affiliate batting and pitching seasons from Baseball-Reference affiliate pages.

It is intentionally separate from your main database/site repo. This repo is the data pipeline. Your database can later read the CSVs from this repo or have this repo write directly to Google Sheets.

## Output files

After the GitHub Action runs, it creates:

- `data/all_batting_seasons.csv`
- `data/all_pitching_seasons.csv`
- `data/run_log.csv`

The two main CSVs are meant to map to your current Google Sheet tabs:

- `All Batting Seasons`
- `All Pitching Seasons`

## Filter-ready columns

The scraper keeps every Baseball-Reference stat column it can find and also adds helper columns, including:

### Both batting and pitching

- `Year`
- `Affiliate`
- `Level`
- `League`
- `Team_URL`
- `Player`

### Batting

- `PA_num`
- `Team_Games_Est`
- `Qualified_Batter`

Qualified batter uses: `PA >= team games × 3.1`

### Pitching

- `IP_num`
- `Team_Games_Est`
- `Qualified_Pitcher`

Qualified pitcher uses: `IP >= team games × 1.0`

## How to upload this into your GitHub repo

You said your repo is named:

`brewers-bref-history`

1. Download and unzip the package from ChatGPT.
2. Open your GitHub repo: `brewers-bref-history`.
3. Click **Add file** → **Upload files**.
4. Drag all unzipped files/folders into GitHub.
5. Make sure the repo root looks like this:

```text
brewers-bref-history/
├── .github/
│   └── workflows/
│       └── update-bref-history.yml
├── data/
│   └── .gitkeep
├── scripts/
│   └── brewers_bref_history.py
├── config.yml
├── requirements.txt
├── .gitignore
└── README.md
```

6. Click **Commit changes**.

## How to run Step 1

1. In your GitHub repo, click the **Actions** tab.
2. Choose **Update Brewers B-Ref History**.
3. Click **Run workflow**.
4. For the first test, enter `2026` in the year box.
5. Click **Run workflow**.

If the 2026 test works, run it again with the year box blank. That will process 1969 through 2026.

## How this integrates into your database

This does **not** need to live in your main database repo.

Recommended structure:

```text
brewers-bref-history repo
  -> creates all_batting_seasons.csv and all_pitching_seasons.csv
  -> later writes those two files into Google Sheets tabs
  -> your existing database/site reads those Google Sheet tabs
```

For now, Step 1 only proves that GitHub can create the CSVs automatically.

Step 2 will connect this repo to your Google Sheet and update the two tabs directly:

- `All Batting Seasons`
- `All Pitching Seasons`

