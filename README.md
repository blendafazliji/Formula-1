# Formula 1 — Relational to NoSQL Migration

A university database project demonstrating the migration of a Formula 1 dataset
from a PostgreSQL relational database to MongoDB. The pipeline covers schema design,
data population, programmatic migration with transformations, automated validation,
and a Streamlit visualization dashboard.

---

## Project Structure

```
Formula-1/
    dataset/                Raw CSV files (Ergast F1 dataset)
        circuits.csv
        constructors.csv
        drivers.csv
        lap_times.csv
        races.csv
        results.csv

    sql/                    PostgreSQL setup scripts
        create_tables.sql   Table definitions
        constraints.sql     Primary keys, foreign keys, check constraints
        import_data.sql     CSV import instructions
        queries.sql         Analytical queries for reference

    diagrams/               ER diagrams (DBeaver + manual)

    migration/
        migration.py        PostgreSQL to MongoDB migration script

    nosql_queries/
        mongodb_queries.js  Equivalent MongoDB aggregation queries

    validation/
        dataValidation.py   Automated validation script (PG vs MongoDB)
        output_sample.txt   Sample validation report output

    visualization/
        f1_dashboard.py     Streamlit dashboard (reads from MongoDB)

    requirements.txt        Python dependencies
    README.md               This file
```

---

## Prerequisites

- Python 3.9 or higher
- PostgreSQL 13 or higher (running locally on port 5432)
- MongoDB 6 or higher (running locally on port 27017)
- pip

---

## Installation

Clone the repository and install Python dependencies:

```bash
git clone https://github.com/blendafazliji/Formula-1.git
cd Formula-1
pip install -r requirements.txt
```

---

## Step 1 — Set Up the Relational Database

These steps are performed once. If you have already loaded the data into PostgreSQL,
skip to Step 2.

**1a. Create the database**

Connect to PostgreSQL and create the target database:

```sql
CREATE DATABASE formula1;
```

**1b. Create tables**

Run the table creation script:

```bash
psql -U postgres -d formula1 -f sql/create_tables.sql
```

**1c. Import the CSV data**

The dataset is imported using DBeaver's CSV Import Wizard or psql COPY commands.
See `sql/import_data.sql` for the correct import order and settings.

Import order (respect foreign key dependencies):
1. circuits
2. constructors
3. drivers
4. races
5. results
6. lap_times

**1d. Apply constraints**

```bash
psql -U postgres -d formula1 -f sql/constraints.sql
```

After this step PostgreSQL should contain approximately:
- circuits: 77 rows
- constructors: 212 rows
- drivers: 861 rows
- races: 1,125 rows
- results: 26,759 rows
- lap_times: 589,081 rows

---

## Step 2 — Run the Migration

The migration script reads from PostgreSQL and writes to MongoDB (database: `f1_nosql`).
It is idempotent — running it more than once will not duplicate data.

**Configure connection details**

Open `migration/migration.py` and update the configuration block at the top if your
PostgreSQL password or database name differs from the defaults:

```python
PG_CONFIG = {
    "host":     "localhost",
    "port":     5432,
    "dbname":   "formula1",
    "user":     "postgres",
    "password": "your_password_here",
}

MONGO_CONFIG = {
    "uri": "mongodb://localhost:27017",
    "db":  "f1_nosql",
}
```

**Run the migration**

```bash
python migration/migration.py
```

The script will log progress to the console. A full run over 589k lap time rows
takes approximately 1 to 3 minutes depending on hardware.

MongoDB collections created:
- drivers
- constructors
- lap_times
- races (embeds circuit and results, includes derived fields)

Derived fields computed during migration:
- totalDrivers — count of drivers per race
- winnerDriverId / winnerDriverName — driver with position = 1
- winnerConstructorId / winnerConstructorName — constructor with position = 1
- fastestLapMs / fastestLapTime — MIN(milliseconds) per driver per race from lap_times
- driverName, driverNationality, constructorName — denormalized into embedded results

---

## Step 3 — Run the Validation

The validation script connects to both databases and compares them across four layers:

- Layer 1: Record counts per entity (including embedded collections)
- Layer 2: Numeric aggregate checksums and an order-independent row hash on lap_times
- Layer 3: Spot-check queries run on both databases and compared
- Layer 4: Derived field checks on a random sample of races

**Configure connection details**

Open `validation/dataValidation.py` and update PG_CONFIG at the top if needed
(same fields as the migration script). Alternatively set environment variables:

```
PG_HOST, PG_PORT, PG_DB, PG_USER, PG_PASSWORD
MONGO_URI, MONGO_DB
```

**Run the validation**

```bash
python validation/dataValidation.py
```

Output is printed to the console and written to `validation_report.log`.
The script exits with code 0 if all checks pass, or code 1 if any check fails.

Note: race 780 produces a known one-check failure due to a data quality issue in the
Ergast source dataset — two drivers are recorded with position = 1 for that race.
This is a source anomaly, not a migration error. All other checks pass.

---

## Step 4 — Run the Visualization Dashboard

The dashboard reads exclusively from MongoDB and requires no PostgreSQL connection.

**Run the dashboard**

```bash
streamlit run visualization/f1_dashboard.py
```

Then open your browser at `http://localhost:8501`.

The dashboard contains six visualizations:
1. Constructor championship points by year
2. Top drivers by race wins
3. Starting grid vs finishing position heatmap
4. Average fastest lap time by circuit
5. Average drivers per race by season
6. Race wins by driver nationality

All visualizations use the derived or denormalized fields produced during migration.
The year range can be filtered from the sidebar.

---

## Dependencies

Listed in `requirements.txt`:

```
psycopg2-binary
pymongo
streamlit
plotly
pandas
```

Install with:

```bash
pip install -r requirements.txt
```

---

## Data Source

Ergast Formula 1 dataset (1950 - 2024), sourced from Kaggle:
https://www.kaggle.com/datasets/rohanrao/formula-1-world-championship-1950-2020

---

## Authors

Blenda Fazliji and Kanita Bajrami
NoSQL Database Course — 2026
