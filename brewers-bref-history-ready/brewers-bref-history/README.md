# Brewers Baseball-Reference Minor League History

This repo scrapes Milwaukee Brewers minor league affiliate player-season data from Baseball-Reference Register pages.

It creates two files:

- `data/all_batting_seasons.csv`
- `data/all_pitching_seasons.csv`

Those files are meant to become the source for your Google Sheet tabs:

- `All Batting Seasons`
- `All Pitching Seasons`

This repo does not need to be inside your main database/site repo. Think of this repo as the data pipeline.

---

## What it does

For each season from 1969 through 2026, it visits:

`https://www.baseball-reference.com/register/affiliate.cgi?id=MIL&year=YEAR`

Then it finds each Brewers affiliate team page for that year and extracts player batting and pitching tables.

The output includes Baseball-Reference stats plus extra filter-friendly fields:

### Batting extra fields

- `Year`
- `Org`
- `Affiliate`
- `Level`
- `League`
- `Team_ID`
- `Team_URL`
- `Player`
- `PA_num`
- `G_num`
- `Qualified_Batter`
- `Source`

### Pitching extra fields

- `Year`
- `Org`
- `Affiliate`
- `Level`
- `League`
- `Team_ID`
- `Team_URL`
- `Player`
- `IP_num`
- `G_num`
- `Qualified_Pitcher`
- `Source`

### Qualification rules

- Qualified batter: `PA >= team games * 3.1`
- Qualified pitcher: `IP >= team games * 1.0`

These are adjustable in `scripts/brewers_bref_history.py`.

---

## Uploading this to GitHub

You already created the repo:

`brewers-bref-history`

Now upload the files from this ZIP.

Your repo should look like this:

```text
brewers-bref-history
├── .github
│   └── workflows
│       └── update-bref-history.yml
├── data
│   └── .gitkeep
├── scripts
│   └── brewers_bref_history.py
├── .gitignore
├── README.md
└── requirements.txt
```

Important: the `.github/workflows/update-bref-history.yml` file must be in the repo before the Actions tab will show the workflow.

---

## First test run

After uploading and committing the files:

1. Go to the repo on GitHub.
2. Click **Actions**.
3. Click **Update Brewers B-Ref History**.
4. Click **Run workflow**.
5. In the `year` box, enter:

```text
2026
```

6. Click **Run workflow**.

If it succeeds, it should commit updated CSV files into the `data/` folder.

---

## Full historical run

After the 2026 test works:

1. Go back to **Actions**.
2. Click **Run workflow**.
3. Leave the `year` field blank.
4. Run it.

That will scrape 1969 through 2026.

---

## How this integrates with your database

For now, this repo generates CSVs.

Next step is connecting those CSVs to your Google Sheet. There are two good options:

### Option A — easiest manual bridge

Use Google Sheets:

```text
File → Import → Upload → Replace current sheet
```

Import:

- `data/all_batting_seasons.csv` into `All Batting Seasons`
- `data/all_pitching_seasons.csv` into `All Pitching Seasons`

### Option B — automatic sync

Add a Google service account and GitHub secret so the action writes directly to your Google Sheet tabs.

That is Step 2. This package is Step 1.
