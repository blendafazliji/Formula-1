# 🏎️ Formula 1 — Relational to NoSQL Migration

Migration of the Formula 1 World Championship dataset from **PostgreSQL** to **MongoDB**, with automated validation and an interactive analytics dashboard.

> **Course:** Databases — South East European University  
> **Authors:** Blenda Fazliji, Kanita  
> **Dataset:** [Ergast F1 Dataset via Kaggle](https://www.kaggle.com/datasets/rohanrao/formula-1-world-championship-1950-2020)

---

## 📁 Repository Structure

```
Formula-1/
├── dataset/              # Raw CSV files (circuits, drivers, constructors, races, results, lap_times)
├── diagrams/             # ER diagram of the PostgreSQL schema
├── sql/                  # SQL schema (CREATE TABLE) and analytical queries
│   ├── schema.sql
│   └── queries.sql
├── migration/
│   └── migrate.py        # PostgreSQL → MongoDB migration script
├── validation/
│   └── validate.py       # Automated validation (PostgreSQL ↔ MongoDB)
├── nosql_queries/
│   └── visualisation.py  # Streamlit analytics dashboard (reads from MongoDB)
└── README.md
```

---

## 🗄️ Database Overview

### PostgreSQL (Source)

| Table | Rows | Description |
|-------|------|-------------|
| circuits | 77 | Race circuits with GPS coordinates |
| drivers | 861 | All F1 drivers |
| constructors | 212 | All F1 teams |
| races | 1,125 | Race events by year and round |
| results | 26,759 | Per-driver race results |
| lap_times | 589,081 | Individual lap records |

### MongoDB (Target) — `f1_nosql`

| Collection | Documents | Notes |
|------------|-----------|-------|
| races | 1,125 | Embeds circuit + all results; contains 5 derived fields |
| drivers | 861 | Standalone collection |
| constructors | 212 | Standalone collection |
| lap_times | 589,081 | Separate high-volume collection |

---

## ⚙️ Prerequisites

Make sure the following are installed and running:

- Python 3.8+
- PostgreSQL (running on port 5432)
- MongoDB (running on port 27017)
- pip packages:

```bash
pip install psycopg2-binary pymongo streamlit plotly pandas
```

---

## 🚀 Running the Full Pipeline

### Step 1 — Set Up the PostgreSQL Database

Open DBeaver (or any PostgreSQL client), connect to your server, and run:

```
sql/schema.sql
```

Then import the CSV files from the `dataset/` folder in this order using DBeaver's CSV Import Wizard:

1. circuits
2. constructors
3. drivers
4. races
5. results
6. lap_times

> CSV settings: Delimiter `,` · Header enabled · NULL value mark `\N` · Encoding UTF-8

---

### Step 2 — Configure Database Credentials

Open `migration/migrate.py` and `validation/validate.py` and fill in your credentials at the top of each file:

```python
PG_CONFIG = {
    "host":     "localhost",
    "port":     5432,
    "dbname":   "formula1",
    "user":     "postgres",
    "password": "your_password",
}

MONGO_CONFIG = {
    "uri": "mongodb://localhost:27017",
    "db":  "f1_nosql",
}
```

---

### Step 3 — Run the Migration

```bash
python migration/migrate.py
```

The script will:
- Migrate drivers, constructors, and lap_times as standalone collections
- Build a rich `races` collection embedding circuit and results data
- Compute 5 derived fields: `fastestLapMs`, `fastestLapTime`, `totalDrivers`, `winnerDriverName`, `winnerConstructorName`
- Create indexes on all collections

**The script is idempotent** — run it twice to verify no duplicates are created:

```bash
python migration/migrate.py   # first run
python migration/migrate.py   # second run — safe, no duplicates
```

---

### Step 4 — Run the Validation

```bash
python validation/validate.py
```

The validation script connects to both databases and checks:

1. **Record counts** — all collections match PostgreSQL row counts
2. **Driver wins checksum** — top 10 driver win counts match between both databases
3. **Constructor wins spot-check** — top 5 constructor wins match
4. **Fastest lap derived field** — sampled `fastestLapMs` values match `MIN(milliseconds)` from `lap_times`

Expected output:

```
============================================================
   F1 DATA VALIDATION (PostgreSQL ↔ MongoDB)
============================================================

--- 1. RECORD COUNT VALIDATION ---
  ⚠  circuits: EMBEDDED IN MONGODB  →  ✔ PASS
  ✔  constructors: PASS (212)
  ✔  drivers: PASS (861)
  ✔  lap_times: PASS (589081)
  ✔  races: PASS (1125)
  ⚠  results: EMBEDDED IN MONGODB  →  ✔ PASS

--- 2. CHECKSUM — Top 10 Driver Wins ---
  ✔  Hamilton: PASS (103)
  ...

--- 3. SPOT CHECK — Top 5 Constructor Wins ---
  ✔  Ferrari: PASS (249)
  ...

--- 4. SPOT CHECK — Derived fastestLapMs ---
  ✔  All sampled fastestLapMs values match.

============================================================
  VALIDATION COMPLETE
============================================================
```

---

### Step 5 — Launch the Visualization Dashboard

```bash
streamlit run nosql_queries/visualisation.py
```

The dashboard opens automatically at **http://localhost:8501**

It includes 6 interactive visualizations, all reading exclusively from MongoDB:

| Tab | Visualization | Derived Field Used |
|-----|--------------|-------------------|
| 1 | Constructor Points by Year | `results.points` (embedded) |
| 2 | Top Drivers by Race Wins | `winnerDriverName` |
| 3 | Grid vs Finish Heatmap | `results.grid`, `results.position` |
| 4 | Avg Fastest Lap by Circuit | `results.fastestLapMs` |
| 5 | Drivers per Race Over Time | `totalDrivers` |
| 6 | Win Rate by Nationality | `winnerDriverId` + denormalized nationality |

---

## 🔄 Derived Fields Computed During Migration

These fields do not exist in the original PostgreSQL schema — they are computed by the migration script:

| Field | Location | Description |
|-------|----------|-------------|
| `fastestLapMs` | `races.results[]` | MIN(milliseconds) per driver per race from lap_times |
| `fastestLapTime` | `races.results[]` | Human-readable format of fastestLapMs (e.g. `1:29.145`) |
| `totalDrivers` | `races` root | Count of embedded result documents per race |
| `winnerDriverName` | `races` root | Full name of the race winner (denormalized) |
| `winnerConstructorName` | `races` root | Constructor name of the race winner (denormalized) |

---

## 📊 NoSQL Design Decisions

| Data | Decision | Reason |
|------|----------|--------|
| Circuit inside race | Embedded | Always fetched with race; never queried alone |
| Results inside race | Embedded | Meaningless without race context |
| Driver/Constructor name in result | Denormalized | Avoids secondary lookups at read time |
| lap_times | Separate collection | 589,081 docs would exceed MongoDB's 16MB document limit |
| drivers / constructors | Separate collections | Queried independently for standings |

---

## 📦 Dependencies

```
psycopg2-binary
pymongo
streamlit
plotly
pandas
```
