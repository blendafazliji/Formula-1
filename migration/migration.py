"""
F1 Data Migration Script: PostgreSQL -> MongoDB
===============================================
Migrates Formula 1 data from a relational PostgreSQL database to MongoDB
using a hybrid document model (embedding + referencing).

Schema reference (actual table columns):
  circuits     : circuitId, circuitRef, name, country, lat, lng
  drivers      : driverId, driverRef, forename, surname, nationality, dob
  constructors : constructorId, constructorRef, name, nationality
  races        : raceId, year, round, circuitId, name, date, time
  results      : resultId, raceId, driverId, constructorId, grid, position, points
  lap_times    : raceId, driverId, lap, position, milliseconds

MongoDB collections produced:
  races        -> embedded circuit + results (with names denormalized), derived fields
  drivers      -> separate, deduplicated, full detail
  constructors -> separate, deduplicated, full detail
  lap_times    -> separate, bulk-inserted per race

Denormalization strategy:
  Driver and constructor names are embedded directly inside each result subdoc
  and in the race-level winner fields. This makes race documents self-contained
  for display without needing $lookup on every read. The separate drivers /
  constructors collections remain the source of truth for full detail.

Usage:
  1. Set PG_CONFIG / MONGO_CONFIG below, or export environment variables.
  2. pip install psycopg2-binary pymongo
  3. python f1_migration.py
"""

import os
import logging
from datetime import datetime

import psycopg2
import psycopg2.extras
from pymongo import MongoClient, UpdateOne, ASCENDING
from pymongo.errors import BulkWriteError

# ─────────────────────────────────────────────────────────────
# 1. CONNECTION CONFIGURATION
# ─────────────────────────────────────────────────────────────

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

# ─────────────────────────────────────────────────────────────
# 2. LOGGING
# ─────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# 3. HELPERS
# ─────────────────────────────────────────────────────────────

def safe(value, default=None):
    """Return value unless it is None or empty string."""
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
    """Convert integer milliseconds to a readable lap time string (M:SS.mmm)."""
    if ms is None:
        return None
    try:
        ms       = int(ms)
        minutes  = ms // 60000
        seconds  = (ms % 60000) / 1000
        return f"{minutes}:{seconds:06.3f}"
    except (TypeError, ValueError):
        return None


def coerce_date(value):
    """
    Convert a psycopg2 date/string to datetime.datetime for BSON compatibility.
    BSON does not accept datetime.date — only datetime.datetime.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if hasattr(value, "year"):                          # datetime.date
        return datetime(value.year, value.month, value.day)
    if isinstance(value, str):
        try:
            return datetime.strptime(value, "%Y-%m-%d")
        except ValueError:
            return None
    return None


# ─────────────────────────────────────────────────────────────
# 4. POSTGRESQL QUERIES
# ─────────────────────────────────────────────────────────────

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

SQL_ALL_CONSTRUCTORS = """
SELECT
    constructorid,
    name,
    nationality
FROM constructors
ORDER BY constructorid;
"""

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


# ─────────────────────────────────────────────────────────────
# 5. IN-MEMORY LOOKUP BUILDERS
# ─────────────────────────────────────────────────────────────

def build_driver_lookup(pg_cur):
    """
    Fetch all drivers once and return a dict keyed by driverId.

    Each entry: { fullName, nationality, dob }
    Used to denormalize names into result subdocs — zero extra DB
    queries during the race loop.
    """
    pg_cur.execute(SQL_ALL_DRIVERS)
    rows   = pg_cur.fetchall()
    lookup = {}
    for row in rows:
        full_name = f"{safe(row['forename'], '')} {safe(row['surname'], '')}".strip()
        lookup[row["driverid"]] = {
            "fullName":    full_name,
            "nationality": safe(row["nationality"]),
            "dob":         coerce_date(row["dob"]),
        }
    log.info("  Driver lookup built (%d drivers).", len(lookup))
    return lookup


def build_constructor_lookup(pg_cur):
    """
    Fetch all constructors once and return a dict keyed by constructorId.

    Each entry: { name, nationality }
    Used to denormalize names into result subdocs — zero extra DB
    queries during the race loop.
    """
    pg_cur.execute(SQL_ALL_CONSTRUCTORS)
    rows   = pg_cur.fetchall()
    lookup = {}
    for row in rows:
        lookup[row["constructorid"]] = {
            "name":        safe(row["name"]),
            "nationality": safe(row["nationality"]),
        }
    log.info("  Constructor lookup built (%d constructors).", len(lookup))
    return lookup


# ─────────────────────────────────────────────────────────────
# 6. MIGRATION FUNCTIONS
# ─────────────────────────────────────────────────────────────

def migrate_drivers(driver_lookup, mongo_db):
    """
    Upsert all drivers into the 'drivers' collection from the in-memory lookup.
    Idempotent via upsert on driverId.
    """
    log.info("Migrating drivers ...")
    ops = []
    for driver_id, info in driver_lookup.items():
        doc = {
            "driverId":    driver_id,
            "fullName":    info["fullName"],
            "nationality": info["nationality"],
            "dob":         info["dob"],
        }
        ops.append(UpdateOne(
            {"driverId": driver_id},
            {"$set": doc},
            upsert=True,
        ))

    if ops:
        result = mongo_db.drivers.bulk_write(ops, ordered=False)
        log.info("  Drivers -> upserted: %d, modified: %d",
                 result.upserted_count, result.modified_count)

    mongo_db.drivers.create_index([("driverId", ASCENDING)], unique=True, background=True)
    log.info("  Drivers done (%d).", len(ops))


def migrate_constructors(constructor_lookup, mongo_db):
    """
    Upsert all constructors into the 'constructors' collection from the in-memory lookup.
    Idempotent via upsert on constructorId.
    """
    log.info("Migrating constructors ...")
    ops = []
    for constructor_id, info in constructor_lookup.items():
        doc = {
            "constructorId": constructor_id,
            "name":          info["name"],
            "nationality":   info["nationality"],
        }
        ops.append(UpdateOne(
            {"constructorId": constructor_id},
            {"$set": doc},
            upsert=True,
        ))

    if ops:
        result = mongo_db.constructors.bulk_write(ops, ordered=False)
        log.info("  Constructors -> upserted: %d, modified: %d",
                 result.upserted_count, result.modified_count)

    mongo_db.constructors.create_index([("constructorId", ASCENDING)], unique=True, background=True)
    log.info("  Constructors done (%d).", len(ops))


def build_circuit_subdoc(race_row):
    """Embed circuit data directly from the race JOIN row."""
    return {
        "circuitId": race_row["circuitid"],
        "name":      safe(race_row["circuit_name"]),
        "country":   safe(race_row["country"]),
        "lat":       to_float(race_row.get("lat")),
        "lng":       to_float(race_row.get("lng")),
    }


def build_result_subdocs(result_rows, driver_lookup, constructor_lookup):
    """
    Convert raw result rows into embedded subdocs, denormalizing driver
    and constructor names from the in-memory lookups.

    Returns (list_of_subdocs, derived_fields_dict).

    Derived fields computed here:
      totalDrivers        : count of result rows
      winnerDriverId      : driverId  where position == 1
      winnerName          : full name where position == 1   [DENORMALIZED]
      winnerConstructorId : constructorId where position == 1
      winnerConstructorName: name where position == 1       [DENORMALIZED]
    """
    embedded              = []
    winner_driver_id      = None
    winner_name           = None
    winner_constructor_id = None
    winner_constructor_nm = None

    for row in result_rows:
        position       = to_int(row["position"])
        driver_id      = row["driverid"]
        constructor_id = row["constructorid"]

        # Resolve names from in-memory lookups (no DB hit)
        driver_info      = driver_lookup.get(driver_id, {})
        constructor_info = constructor_lookup.get(constructor_id, {})

        subdoc = {
            "resultId":           row["resultid"],
            # References (IDs kept for joins / filtering)
            "driverId":           driver_id,
            "constructorId":      constructor_id,
            # Denormalized display names (no $lookup needed)
            "driverName":         driver_info.get("fullName"),
            "driverNationality":  driver_info.get("nationality"),
            "constructorName":    constructor_info.get("name"),
            # Race result fields
            "grid":               to_int(row["grid"]),
            "position":           position,
            "points":             to_float(row["points"], default=0.0),
            # fastestLap attached later after lap_times are processed
        }
        embedded.append(subdoc)

        if position == 1:
            winner_driver_id      = driver_id
            winner_name           = driver_info.get("fullName")
            winner_constructor_id = constructor_id
            winner_constructor_nm = constructor_info.get("name")

    derived = {
        "totalDrivers":           len(embedded),
        "winnerDriverId":         winner_driver_id,
        "winnerName":             winner_name,           # DENORMALIZED
        "winnerConstructorId":    winner_constructor_id,
        "winnerConstructorName":  winner_constructor_nm, # DENORMALIZED
    }
    return embedded, derived


def compute_fastest_laps(lap_rows):
    """
    Compute the fastest lap (minimum milliseconds) per driver for one race.
    Returns dict: { driverId -> { lap, milliseconds, time } }
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
    Bulk-upsert lap_times rows for one race.
    Each row is flagged fastestLap=True if it matches the driver's best lap.
    Idempotent via composite key (raceId, driverId, lap).
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
            "time":         ms_to_laptime(ms),
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


def migrate_race(pg_cur, mongo_db, race_row, driver_lookup, constructor_lookup):
    """
    Migrate one race document:
      1. Embed circuit (from JOIN columns in race_row)
      2. Fetch & embed results with denormalized driver/constructor names
      3. Compute derived fields: totalDrivers, winner name + IDs
      4. Fetch lap times; compute per-driver fastest lap
      5. Attach fastestLap to each embedded result
      6. Upsert full race document into 'races'
      7. Bulk-upsert lap rows into 'lap_times'
    """
    race_id = race_row["raceid"]

    # 1. Circuit (embedded)
    circuit_doc = build_circuit_subdoc(race_row)

    # 2 & 3. Results with names + derived fields
    pg_cur.execute(SQL_RESULTS_FOR_RACE, {"raceid": race_id})
    result_rows = pg_cur.fetchall()
    results_docs, derived = build_result_subdocs(
        result_rows, driver_lookup, constructor_lookup
    )

    # 4. Lap times
    pg_cur.execute(SQL_LAP_TIMES_FOR_RACE, {"raceid": race_id})
    lap_rows     = pg_cur.fetchall()
    fastest_laps = compute_fastest_laps(lap_rows)

    # 5. Attach fastest lap summary to each embedded result
    for res in results_docs:
        res["fastestLap"] = fastest_laps.get(res["driverId"])   # dict or None

    # 6. Build race document
    race_doc = {
        "raceId":  race_id,
        "year":    to_int(race_row["year"]),
        "round":   to_int(race_row["round"]),
        "name":    safe(race_row["race_name"]),
        "date":    coerce_date(race_row["date"]),
        "time":    safe(race_row["race_time"]),

        # EMBEDDED sub-documents
        "circuit": circuit_doc,
        "results": results_docs,

        # Derived / denormalized race-level fields
        "totalDrivers":          derived["totalDrivers"],
        "winnerDriverId":        derived["winnerDriverId"],
        "winnerName":            derived["winnerName"],            # DENORMALIZED
        "winnerConstructorId":   derived["winnerConstructorId"],
        "winnerConstructorName": derived["winnerConstructorName"], # DENORMALIZED
    }

    mongo_db.races.update_one(
        {"raceId": race_id},
        {"$set": race_doc},
        upsert=True,
    )

    # 7. Lap times (separate collection)
    return migrate_lap_times(mongo_db, race_id, lap_rows, fastest_laps)


def ensure_indexes(mongo_db):
    """Create all indexes before migration starts (incremental build)."""
    log.info("Ensuring MongoDB indexes ...")

    mongo_db.races.create_index([("raceId", ASCENDING)],               unique=True, background=True)
    mongo_db.races.create_index([("year", ASCENDING)],                              background=True)
    mongo_db.races.create_index([("winnerDriverId", ASCENDING)],                    background=True)
    mongo_db.races.create_index([("winnerName", ASCENDING)],                        background=True)
    mongo_db.races.create_index([("circuit.circuitId", ASCENDING)],                 background=True)

    mongo_db.drivers.create_index([("driverId", ASCENDING)],           unique=True, background=True)
    mongo_db.constructors.create_index([("constructorId", ASCENDING)],  unique=True, background=True)

    mongo_db.lap_times.create_index(
        [("raceId", ASCENDING), ("driverId", ASCENDING), ("lap", ASCENDING)],
        unique=True, background=True,
    )
    mongo_db.lap_times.create_index([("driverId", ASCENDING)], background=True)

    log.info("  Indexes OK.")


# ─────────────────────────────────────────────────────────────
# 7. MAIN
# ─────────────────────────────────────────────────────────────

def main():
    log.info("=" * 60)
    log.info("F1 Migration: PostgreSQL -> MongoDB")
    log.info("=" * 60)

    # Connect to PostgreSQL
    log.info("Connecting to PostgreSQL ...")
    try:
        pg_conn = psycopg2.connect(**PG_CONFIG)
        pg_cur  = pg_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        log.info("  PostgreSQL OK.")
    except Exception as exc:
        log.error("PostgreSQL connection failed: %s", exc)
        raise

    # Connect to MongoDB
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

        # Build in-memory name lookups (2 queries total, reused across all 1125 races)
        log.info("Building in-memory lookups ...")
        driver_lookup      = build_driver_lookup(pg_cur)
        constructor_lookup = build_constructor_lookup(pg_cur)

        # Migrate lookup collections to MongoDB (reuses the same dicts)
        migrate_drivers(driver_lookup, mongo_db)
        migrate_constructors(constructor_lookup, mongo_db)

        # Migrate all races
        log.info("Fetching all races ...")
        pg_cur.execute(SQL_ALL_RACES)
        race_rows   = pg_cur.fetchall()
        total_races = len(race_rows)
        log.info("  Found %d races.", total_races)

        total_lap_rows = 0
        for idx, race_row in enumerate(race_rows, start=1):
            lt_count        = migrate_race(
                pg_cur, mongo_db, race_row, driver_lookup, constructor_lookup
            )
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
