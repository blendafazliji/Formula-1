"""
F1 Data Migration Script: PostgreSQL → MongoDB
===============================================
Migrates Formula 1 data from a relational PostgreSQL database to MongoDB
using a hybrid document model (embedding + referencing).

Schema reference (actual table columns):
  circuits  : circuitId, circuitRef, name, country, lat, lng
  drivers   : driverId, driverRef, forename, surname, nationality, dob
  constructors: constructorId, constructorRef, name, nationality
  races     : raceId, year, round, circuitId, name, date, time
  results   : resultId, raceId, driverId, constructorId, grid, position, points
  lap_times : raceId, driverId, lap, position, milliseconds

Collections produced:
  - races        → embedded circuit + results, with derived fields
  - drivers      → separate, deduplicated
  - constructors → separate, deduplicated
  - lap_times    → separate, bulk-inserted per race

Usage:
  1. Fill in PG_CONFIG and MONGO_CONFIG below (or set env vars).
  2. pip install psycopg2-binary pymongo
  3. python f1_migration.py
"""

import os
import logging
from datetime import datetime

import psycopg2
import psycopg2.extras          # RealDictCursor
from pymongo import MongoClient, UpdateOne, ASCENDING
from pymongo.errors import BulkWriteError

# ──────────────────────────────────────────────
# 1. CONNECTION CONFIGURATION
# ──────────────────────────────────────────────

PG_CONFIG = {
    "host":     os.getenv("PG_HOST",     "localhost"),
    "port":     int(os.getenv("PG_PORT", "5432")),
    "dbname":   os.getenv("PG_DB",       "formula1"),
    "user":     os.getenv("PG_USER",     "postgres"),
    "password": os.getenv("PG_PASSWORD", "password"),
}

MONGO_CONFIG = {
    "uri":    os.getenv("MONGO_URI",    "mongodb://localhost:27017"),
    "dbname": os.getenv("MONGO_DB",     "formula1"),
}

# ──────────────────────────────────────────────
# 2. LOGGING
# ──────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# 3. HELPERS
# ──────────────────────────────────────────────

def safe(value, default=None):
    """Return value if not None/empty-string, else default."""
    if value is None or value == "":
        return default
    return value


def to_int(value, default=None):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def to_float(value, default=None):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def ms_to_laptime(ms):
    """Convert milliseconds integer to a human-readable lap time string (M:SS.mmm)."""
    if ms is None:
        return None
    try:
        ms = int(ms)
        minutes  = ms // 60000
        seconds  = (ms % 60000) / 1000
        return f"{minutes}:{seconds:06.3f}"
    except (TypeError, ValueError):
        return None


# ──────────────────────────────────────────────
# 4. POSTGRESQL QUERIES
# (Aligned exactly to the confirmed schema)
# ──────────────────────────────────────────────

# circuits columns: circuitId, circuitRef, name, country, lat, lng
# races    columns: raceId, year, round, circuitId, name, date, time
SQL_ALL_RACES = """
SELECT
    r.raceid,
    r.year,
    r.round,
    r.name   AS race_name,
    r.date,
    r.time   AS race_time,
    c.circuitid,
    c.name   AS circuit_name,
    c.country,
    c.lat,
    c.lng
FROM races r
JOIN circuits c ON c.circuitid = r.circuitid
ORDER BY r.year, r.round;
"""

# results columns: resultId, raceId, driverId, constructorId, grid, position, points
# NOTE: no statusId / status column in this schema
SQL_RESULTS_FOR_RACE = """
SELECT
    resultid,
    raceid,
    driverid,
    constructorid,
    grid,
    position,
    points
FROM results
WHERE raceid = %(raceid)s
ORDER BY position NULLS LAST;
"""

# drivers columns: driverId, driverRef, forename, surname, nationality, dob
SQL_ALL_DRIVERS = """
SELECT
    driverid,
    forename,
    surname,
    nationality,
    dob
FROM drivers
ORDER BY driverid;
"""

# constructors columns: constructorId, constructorRef, name, nationality
SQL_ALL_CONSTRUCTORS = """
SELECT
    constructorid,
    name,
    nationality
FROM constructors
ORDER BY constructorid;
"""

# lap_times columns: raceId, driverId, lap, position, milliseconds
# NOTE: no 'time' text column in this schema — derive it from milliseconds
SQL_LAP_TIMES_FOR_RACE = """
SELECT
    raceid,
    driverid,
    lap,
    position,
    milliseconds
FROM lap_times
WHERE raceid = %(raceid)s
ORDER BY lap, position;
"""


# ──────────────────────────────────────────────
# 5. MIGRATION FUNCTIONS
# ──────────────────────────────────────────────

def migrate_drivers(pg_cur, mongo_db):
    """
    Migrate all drivers to the 'drivers' collection.
    Uses upsert on driverId to ensure idempotency.
    """
    log.info("Migrating drivers ...")
    pg_cur.execute(SQL_ALL_DRIVERS)
    rows = pg_cur.fetchall()

    ops = []
    for row in rows:
        # BSON requires datetime.datetime, not datetime.date
        dob = row["dob"]
        if hasattr(dob, "year") and not isinstance(dob, datetime):
            dob = datetime(dob.year, dob.month, dob.day)

        doc = {
            "driverId":    row["driverid"],
            "fullName":    f"{safe(row['forename'], '')} {safe(row['surname'], '')}".strip(),
            "nationality": safe(row["nationality"]),
            "dob":         dob,   # datetime.datetime or None
        }
        ops.append(UpdateOne(
            {"driverId": doc["driverId"]},
            {"$set": doc},
            upsert=True,
        ))

    if ops:
        result = mongo_db.drivers.bulk_write(ops, ordered=False)
        log.info("  Drivers -> upserted: %d, modified: %d",
                 result.upserted_count, result.modified_count)

    mongo_db.drivers.create_index([("driverId", ASCENDING)], unique=True, background=True)
    log.info("  Drivers done (%d rows).", len(rows))


def migrate_constructors(pg_cur, mongo_db):
    """
    Migrate all constructors to the 'constructors' collection.
    Uses upsert on constructorId to ensure idempotency.
    """
    log.info("Migrating constructors ...")
    pg_cur.execute(SQL_ALL_CONSTRUCTORS)
    rows = pg_cur.fetchall()

    ops = []
    for row in rows:
        doc = {
            "constructorId": row["constructorid"],
            "name":          safe(row["name"]),
            "nationality":   safe(row["nationality"]),
        }
        ops.append(UpdateOne(
            {"constructorId": doc["constructorId"]},
            {"$set": doc},
            upsert=True,
        ))

    if ops:
        result = mongo_db.constructors.bulk_write(ops, ordered=False)
        log.info("  Constructors -> upserted: %d, modified: %d",
                 result.upserted_count, result.modified_count)

    mongo_db.constructors.create_index([("constructorId", ASCENDING)], unique=True, background=True)
    log.info("  Constructors done (%d rows).", len(rows))


def build_circuit_subdoc(race_row):
    """
    Build the embedded circuit sub-document from the race JOIN row.
    Schema: circuitId, name, country, lat, lng  (no locality column).
    """
    return {
        "circuitId": race_row["circuitid"],
        "name":      safe(race_row["circuit_name"]),
        "country":   safe(race_row["country"]),
        "lat":       to_float(race_row.get("lat")),
        "lng":       to_float(race_row.get("lng")),
    }


def build_result_subdocs(result_rows):
    """
    Convert raw result rows into embedded sub-documents.
    Returns (list_of_subdocs, derived_fields_dict).

    Derived fields:
      - totalDrivers        : count of result rows
      - winnerDriverId      : driverId where position == 1
      - winnerConstructorId : constructorId where position == 1
    """
    embedded = []
    winner_driver_id      = None
    winner_constructor_id = None

    for row in result_rows:
        position = to_int(row["position"])

        subdoc = {
            "resultId":      row["resultid"],
            "driverId":      row["driverid"],
            "constructorId": row["constructorid"],
            "grid":          to_int(row["grid"]),
            "position":      position,
            "points":        to_float(row["points"], default=0.0),
            # fastestLap will be attached later once lap_times are processed
        }
        embedded.append(subdoc)

        if position == 1:
            winner_driver_id      = row["driverid"]
            winner_constructor_id = row["constructorid"]

    derived = {
        "totalDrivers":        len(embedded),
        "winnerDriverId":      winner_driver_id,
        "winnerConstructorId": winner_constructor_id,
    }
    return embedded, derived


def compute_fastest_laps(lap_rows):
    """
    Compute the fastest lap per driver (minimum milliseconds) for a race.

    Returns dict: { driverId -> { lap, milliseconds, time } }
    where 'time' is the human-readable string derived from milliseconds.
    """
    fastest = {}
    for row in lap_rows:
        ms = to_int(row["milliseconds"])
        if ms is None:
            continue
        driver_id = row["driverid"]
        if driver_id not in fastest or ms < fastest[driver_id]["milliseconds"]:
            fastest[driver_id] = {
                "lap":          to_int(row["lap"]),
                "milliseconds": ms,
                "time":         ms_to_laptime(ms),
            }
    return fastest


def migrate_lap_times(mongo_db, race_id, lap_rows, fastest_laps):
    """
    Bulk-upsert lap_times rows for one race into the 'lap_times' collection.
    Each row is flagged with fastestLap=True/False.
    Idempotent via upsert on composite key (raceId, driverId, lap).
    """
    if not lap_rows:
        return 0

    ops = []
    for row in lap_rows:
        driver_id = row["driverid"]
        lap       = to_int(row["lap"])
        ms        = to_int(row["milliseconds"])

        is_fastest = (
            driver_id in fastest_laps
            and lap is not None
            and lap == fastest_laps[driver_id]["lap"]
        )

        doc = {
            "raceId":       race_id,
            "driverId":     driver_id,
            "lap":          lap,
            "position":     to_int(row["position"]),
            "milliseconds": ms,
            "time":         ms_to_laptime(ms),   # derived from ms
            "fastestLap":   is_fastest,
        }
        ops.append(UpdateOne(
            {"raceId": race_id, "driverId": driver_id, "lap": lap},
            {"$set": doc},
            upsert=True,
        ))

    try:
        mongo_db.lap_times.bulk_write(ops, ordered=False)
    except BulkWriteError as exc:
        log.warning("  Lap times partial error for race %d: %s",
                    race_id, exc.details.get("writeErrors", [])[:3])

    return len(ops)


def migrate_race(pg_cur, mongo_db, race_row):
    """
    Migrate one race document:
      1. Build embedded circuit sub-doc (from JOIN columns)
      2. Fetch & embed results; compute winner / totalDrivers
      3. Fetch lap times; compute fastest lap per driver
      4. Attach fastestLap summary to each embedded result
      5. Upsert full race document into 'races'
      6. Bulk-upsert lap rows into 'lap_times'
    """
    race_id = race_row["raceid"]

    # ── 1. Circuit (embedded) ───────────────────
    circuit_doc = build_circuit_subdoc(race_row)

    # ── 2. Results (embedded) ───────────────────
    pg_cur.execute(SQL_RESULTS_FOR_RACE, {"raceid": race_id})
    result_rows = pg_cur.fetchall()
    results_docs, derived = build_result_subdocs(result_rows)

    # ── 3. Lap times ────────────────────────────
    pg_cur.execute(SQL_LAP_TIMES_FOR_RACE, {"raceid": race_id})
    lap_rows     = pg_cur.fetchall()
    fastest_laps = compute_fastest_laps(lap_rows)

    # ── 4. Attach fastest lap to each result ────
    for res in results_docs:
        fl = fastest_laps.get(res["driverId"])
        res["fastestLap"] = fl   # dict with lap/ms/time, or None

    # ── 5. Normalise race date ───────────────────
    race_date = race_row["date"]
    if isinstance(race_date, str):
        try:
            race_date = datetime.strptime(race_date, "%Y-%m-%d")
        except ValueError:
            pass   # keep as-is if format differs

    # ── 6. Build and upsert race document ────────
    race_doc = {
        "raceId":              race_id,
        "year":                to_int(race_row["year"]),
        "round":               to_int(race_row["round"]),
        "name":                safe(race_row["race_name"]),
        "date":                race_date,
        "time":                safe(race_row["race_time"]),
        "circuit":             circuit_doc,          # EMBEDDED
        "results":             results_docs,         # EMBEDDED
        # Derived fields
        "totalDrivers":        derived["totalDrivers"],
        "winnerDriverId":      derived["winnerDriverId"],
        "winnerConstructorId": derived["winnerConstructorId"],
    }

    mongo_db.races.update_one(
        {"raceId": race_id},
        {"$set": race_doc},
        upsert=True,
    )

    # ── 7. Upsert lap times (separate collection) ─
    lt_count = migrate_lap_times(mongo_db, race_id, lap_rows, fastest_laps)

    return lt_count


def ensure_indexes(mongo_db):
    """Create MongoDB indexes before migration begins (incremental build)."""
    log.info("Ensuring MongoDB indexes ...")

    # races
    mongo_db.races.create_index([("raceId", ASCENDING)],              unique=True,  background=True)
    mongo_db.races.create_index([("year", ASCENDING)],                               background=True)
    mongo_db.races.create_index([("winnerDriverId", ASCENDING)],                     background=True)
    mongo_db.races.create_index([("circuit.circuitId", ASCENDING)],                  background=True)

    # drivers
    mongo_db.drivers.create_index([("driverId", ASCENDING)],          unique=True,  background=True)

    # constructors
    mongo_db.constructors.create_index([("constructorId", ASCENDING)], unique=True, background=True)

    # lap_times
    mongo_db.lap_times.create_index(
        [("raceId", ASCENDING), ("driverId", ASCENDING), ("lap", ASCENDING)],
        unique=True, background=True,
    )
    mongo_db.lap_times.create_index([("driverId", ASCENDING)], background=True)

    log.info("  Indexes OK.")


# ──────────────────────────────────────────────
# 6. MAIN
# ──────────────────────────────────────────────

def main():
    log.info("=" * 60)
    log.info("F1 Migration: PostgreSQL -> MongoDB")
    log.info("=" * 60)

    # ── PostgreSQL ──────────────────────────────
    log.info("Connecting to PostgreSQL ...")
    try:
        pg_conn = psycopg2.connect(**PG_CONFIG)
        pg_cur  = pg_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        log.info("  PostgreSQL OK.")
    except Exception as exc:
        log.error("PostgreSQL connection failed: %s", exc)
        raise

    # ── MongoDB ─────────────────────────────────
    log.info("Connecting to MongoDB ...")
    try:
        mongo_client = MongoClient(MONGO_CONFIG["uri"], serverSelectionTimeoutMS=5000)
        mongo_client.server_info()
        mongo_db = mongo_client[MONGO_CONFIG["dbname"]]
        log.info("  MongoDB OK (db: %s).", MONGO_CONFIG["dbname"])
    except Exception as exc:
        log.error("MongoDB connection failed: %s", exc)
        pg_conn.close()
        raise

    try:
        ensure_indexes(mongo_db)

        migrate_drivers(pg_cur, mongo_db)
        migrate_constructors(pg_cur, mongo_db)

        log.info("Fetching all races ...")
        pg_cur.execute(SQL_ALL_RACES)
        race_rows   = pg_cur.fetchall()
        total_races = len(race_rows)
        log.info("  Found %d races.", total_races)

        total_lap_rows = 0
        for idx, race_row in enumerate(race_rows, start=1):
            lt_count        = migrate_race(pg_cur, mongo_db, race_row)
            total_lap_rows += lt_count

            if idx % 50 == 0 or idx == total_races:
                log.info("  Progress: %d / %d races done ...", idx, total_races)

        log.info("=" * 60)
        log.info("Migration complete.")
        log.info("  Races migrated   : %d", total_races)
        log.info("  Lap rows migrated: %d", total_lap_rows)
        log.info("=" * 60)

    finally:
        pg_cur.close()
        pg_conn.close()
        mongo_client.close()
        log.info("Connections closed.")


if __name__ == "__main__":
    main()
