"""
============================================================
  Formula 1 Data Migration: PostgreSQL → MongoDB
  Hybrid document model (embedding + referencing)
============================================================
Schema source (exact):
    circuits   : circuitId, circuitRef, name, country, lat, lng
    drivers    : driverId, driverRef, forename, surname, nationality, dob
    constructors: constructorId, constructorRef, name, nationality
    races      : raceId, year, round, circuitId, name, date, time
    results    : resultId, raceId, driverId, constructorId, grid, position, points
    lap_times  : raceId, driverId, lap, position, milliseconds

Dependencies:
    pip install psycopg2-binary pymongo

Usage:
    1. Fill in PG_CONFIG and MONGO_CONFIG below.
    2. python f1_migration.py
============================================================
"""

import logging
import datetime as dt
from datetime import datetime

import psycopg2
import psycopg2.extras
from pymongo import MongoClient, UpdateOne
from pymongo.errors import BulkWriteError


# ──────────────────────────────────────────────────────────
# CONFIGURATION  ← edit before running
# ──────────────────────────────────────────────────────────

PG_CONFIG = {
    "host":     "localhost",
    "port":     5432,
    "dbname":   "formula1",
    "user":     "postgres",
    "password": "kanita",
}

MONGO_CONFIG = {
    "uri": "mongodb://localhost:27017",
    "db":  "f1_nosql",
}

BATCH_SIZE = 500      # rows per MongoDB bulk-write (drivers, constructors, races)
LAP_CHUNK  = 5_000   # rows per streaming chunk for the large lap_times table


# ──────────────────────────────────────────────────────────
# LOGGING
# ──────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────
# HELPERS
# ──────────────────────────────────────────────────────────

def safe_int(value):
    """Cast to int, return None on NULL or non-numeric input."""
    if value is None:
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def safe_float(value):
    """Cast to float, return None on NULL or non-numeric input."""
    if value is None:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def safe_date(value):
    """
    Convert a PostgreSQL DATE (psycopg2 returns datetime.date) to
    datetime.datetime, which is the only date type BSON/PyMongo accepts.
    Strings (races.date is VARCHAR) are parsed to datetime as well.
    Returns None for None / unparseable values.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, dt.date):                     # bare date → midnight datetime
        return datetime(value.year, value.month, value.day)
    if isinstance(value, str):
        for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d"):
            try:
                return datetime.strptime(value.strip(), fmt)
            except ValueError:
                continue
    return None                                        # give up → store null


def bulk_upsert(collection, operations):
    """
    Execute a list of UpdateOne(upsert=True) ops in one batch.
    ordered=False means one bad document never blocks the rest.
    """
    if not operations:
        return
    try:
        result = collection.bulk_write(operations, ordered=False)
        log.debug(
            "  '%s': upserted=%d modified=%d",
            collection.name, result.upserted_count, result.modified_count,
        )
    except BulkWriteError as bwe:
        log.warning(
            "Partial bulk-write error in '%s': %s",
            collection.name,
            bwe.details.get("writeErrors", [])[:3],
        )


# ──────────────────────────────────────────────────────────
# STEP 1 — drivers  (separate collection)
# ──────────────────────────────────────────────────────────

def migrate_drivers(pg_cur, mongo_db):
    """
    drivers table → MongoDB 'drivers' collection.

    Columns used:
        driverId, driverRef, forename, surname, nationality, dob (DATE)
    MongoDB field names follow the spec (forname, not forename).
    """
    log.info("── [1/6] Migrating drivers …")

    pg_cur.execute("""
        SELECT
            driverid,
            driverref,
            forename,
            surname,
            nationality,
            dob
        FROM drivers
        ORDER BY driverid
    """)

    ops = []
    rows = pg_cur.fetchall()
    for r in rows:
        doc = {
            "driverid":    r["driverid"],
            "driverref":   r["driverref"],
            "forname":     r["forename"],      # spec calls it "forname"
            "surname":     r["surname"],
            "nationality": r["nationality"],
            "dob":         safe_date(r["dob"]), # DATE → datetime
        }
        ops.append(UpdateOne({"driverid": doc["driverid"]}, {"$set": doc}, upsert=True))
        if len(ops) >= BATCH_SIZE:
            bulk_upsert(mongo_db.drivers, ops)
            ops = []

    bulk_upsert(mongo_db.drivers, ops)
    log.info("   ✓ %d drivers processed.", len(rows))


# ──────────────────────────────────────────────────────────
# STEP 2 — constructors  (separate collection)
# ──────────────────────────────────────────────────────────

def migrate_constructors(pg_cur, mongo_db):
    """
    constructors table → MongoDB 'constructors' collection.

    Columns used:
        constructorId, constructorRef, name, nationality
    """
    log.info("── [2/6] Migrating constructors …")

    pg_cur.execute("""
        SELECT
            constructorid,
            constructorref,
            name,
            nationality
        FROM constructors
        ORDER BY constructorid
    """)

    ops = []
    rows = pg_cur.fetchall()
    for r in rows:
        doc = {
            "constructorid":  r["constructorid"],
            "constructorref": r["constructorref"],
            "name":           r["name"],
            "nationality":    r["nationality"],
        }
        ops.append(
            UpdateOne({"constructorid": doc["constructorid"]}, {"$set": doc}, upsert=True)
        )
        if len(ops) >= BATCH_SIZE:
            bulk_upsert(mongo_db.constructors, ops)
            ops = []

    bulk_upsert(mongo_db.constructors, ops)
    log.info("   ✓ %d constructors processed.", len(rows))


# ──────────────────────────────────────────────────────────
# STEP 3 — lap_times  (separate collection, high-volume)
# ──────────────────────────────────────────────────────────

def migrate_lap_times(pg_cur, mongo_db):
    """
    lap_times table → MongoDB 'lap_times' collection.

    Columns used:
        raceId, driverId, lap, position, milliseconds
    Primary key in Postgres: (raceId, driverId, lap) — mirrored as unique
    index in Mongo after the bulk load.

    Performance strategy:
      • DROP + recreate   — skips costly per-row upsert lookups on re-runs.
      • Server-side cursor — Postgres streams LAP_CHUNK rows per round-trip;
                             no full-table RAM load on the Python side.
      • insert_many(ordered=False) — one network round-trip per chunk.
      • Indexes built AFTER the load — 10-50× faster than live maintenance.
    """
    log.info("── [3/6] Migrating lap_times …")

    pg_cur.execute("SELECT COUNT(*) AS n FROM lap_times")
    total_rows = pg_cur.fetchone()["n"]
    log.info("   PostgreSQL row count: %d", total_rows)

    mongo_db.lap_times.drop()
    log.info("   Existing lap_times collection dropped (clean slate).")

    inserted = 0

    with pg_cur.connection.cursor(
        name="lap_times_ss_cur",
        cursor_factory=psycopg2.extras.RealDictCursor,
    ) as ss_cur:
        ss_cur.itersize = LAP_CHUNK
        ss_cur.execute("""
            SELECT
                raceid,
                driverid,
                lap,
                position,
                milliseconds
            FROM lap_times
            ORDER BY raceid, driverid, lap
        """)

        batch = []
        for r in ss_cur:
            batch.append({
                "raceid":       r["raceid"],
                "driverid":     r["driverid"],
                "lap":          r["lap"],
                "position":     safe_int(r["position"]),
                "milliseconds": safe_int(r["milliseconds"]),
            })
            if len(batch) >= LAP_CHUNK:
                mongo_db.lap_times.insert_many(batch, ordered=False)
                inserted += len(batch)
                batch = []
                pct = inserted / total_rows * 100 if total_rows else 0
                log.info("   … %d / %d rows (%.1f %%)", inserted, total_rows, pct)

        if batch:
            mongo_db.lap_times.insert_many(batch, ordered=False)
            inserted += len(batch)

    log.info("   ✓ %d lap_time rows inserted.", inserted)
    log.info("   Building lap_times indexes …")
    mongo_db.lap_times.create_index(
        [("raceid", 1), ("driverid", 1), ("lap", 1)], unique=True
    )
    mongo_db.lap_times.create_index([("raceid", 1), ("driverid", 1)])
    log.info("   ✓ lap_times indexes created.")


# ──────────────────────────────────────────────────────────
# STEP 4 — pre-fetch lookup maps  (used by migrate_races)
# ──────────────────────────────────────────────────────────

def fetch_circuits_map(pg_conn):
    """
    Load all circuits into {circuitid: doc}.

    Columns used:
        circuitId, circuitRef, name, country, lat, lng
    Note: there is no 'location' text column — coordinates are lat/lng floats.
    """
    with pg_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
            SELECT
                circuitid,
                circuitref,
                name,
                country,
                lat,
                lng
            FROM circuits
        """)
        return {
            r["circuitid"]: {
                "circuitId":  r["circuitid"],
                "circuitRef": r["circuitref"],
                "name":       r["name"],
                "country":    r["country"],
                "lat":        safe_float(r["lat"]),
                "lng":        safe_float(r["lng"]),
            }
            for r in cur.fetchall()
        }


def fetch_results_map(pg_conn):
    """
    Load all results into {raceid: [result_doc, …]}.

    JOINs drivers and constructors so that each embedded result carries
    the driver full name and constructor name — avoids a second lookup
    when querying races documents.
    """
    with pg_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
            SELECT
                r.raceid,
                r.driverid,
                r.constructorid,
                r.grid,
                r.position,
                r.points,
                d.forename          AS driver_forename,
                d.surname           AS driver_surname,
                d.nationality       AS driver_nationality,
                c.name              AS constructor_name,
                c.nationality       AS constructor_nationality
            FROM results r
            JOIN drivers      d ON d.driverid      = r.driverid
            JOIN constructors c ON c.constructorid  = r.constructorid
            ORDER BY r.raceid, r.position
        """)
        rows = cur.fetchall()

    results_map = {}
    for r in rows:
        raceid = r["raceid"]
        results_map.setdefault(raceid, []).append({
            "raceid":                 raceid,
            "driverid":               r["driverid"],
            "driverName":             f"{r['driver_forename']} {r['driver_surname']}",
            "driverNationality":      r["driver_nationality"],
            "constructorid":          r["constructorid"],
            "constructorName":        r["constructor_name"],
            "constructorNationality": r["constructor_nationality"],
            "grid":                   safe_int(r["grid"]),
            "position":               safe_int(r["position"]),
            "points":                 safe_float(r["points"]),
            # fastestLapMs + fastestLapTime filled in during migrate_races()
            "fastestLapMs":           None,
            "fastestLapTime":         None,
        })
    return results_map


def fetch_fastest_laps_map(pg_conn):
    """
    Aggregate MIN(milliseconds) per (raceId, driverId) from lap_times.
    Returns {(raceid, driverid): min_milliseconds}.

    This is the derived fastestLap field required by the spec.
    """
    """
    Run on a dedicated cursor so it is never affected by the state of the
    server-side cursor used in migrate_lap_times().
    """
    log.info("   Pre-computing fastest laps from lap_times …")
    with pg_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
            SELECT
                raceid,
                driverid,
                MIN(milliseconds) AS min_ms
            FROM lap_times
            GROUP BY raceid, driverid
        """)
        result = {
            (r["raceid"], r["driverid"]): r["min_ms"]
            for r in cur.fetchall()
        }
    log.info("   fastest_laps_map size: %d entries", len(result))
    if result:
        sample_key, sample_val = next(iter(result.items()))
        log.info(
            "   sample entry → key=%s (types: %s, %s) val=%s",
            sample_key,
            type(sample_key[0]).__name__,
            type(sample_key[1]).__name__,
            sample_val,
        )
    return result


# ──────────────────────────────────────────────────────────
# STEP 5 — races  (main collection — embeds circuit + results)
# ──────────────────────────────────────────────────────────

def migrate_races(pg_cur, mongo_db, circuits_map, results_map, fastest_laps_map):
    """
    races table → MongoDB 'races' collection (main/richest document).

    Columns used:
        raceId, year, round, circuitId, name, date (VARCHAR), time (VARCHAR)

    Embedded sub-documents:
        circuit  — full circuit object (lat/lng)
        results  — list of result objects (one per driver entry)

    Derived fields computed here:
        totalDrivers        count of embedded result docs
        winnerDriverId      driverid where position == 1  (None if no winner)
        winnerConstructorId constructorid where position == 1
        fastestLapMs        MIN(milliseconds) from lap_times per driver
        fastestLapTime      human-readable string derived from fastestLapMs
    """
    log.info("── [5/6] Migrating races …")

    pg_cur.execute("""
        SELECT
            raceid,
            year,
            round,
            circuitid,
            name,
            date,
            time
        FROM races
        ORDER BY year, round
    """)
    races = pg_cur.fetchall()

    ops = []
    for r in races:
        raceid    = r["raceid"]
        circuitid = r["circuitid"]

        # ── Embedded circuit (fallback to null fields if id not found)
        circuit_doc = circuits_map.get(circuitid, {
            "circuitId":  circuitid,
            "circuitRef": None,
            "name":       None,
            "country":    None,
            "lat":        None,
            "lng":        None,
        })

        # ── Embedded results list
        race_results = results_map.get(raceid, [])

        # ── Derived: totalDrivers
        total_drivers = len(race_results)

        # ── Derived: winner (first result where position == 1)
        winner_driver_id      = None
        winner_driver_name    = None
        winner_constructor_id = None
        winner_constructor_name = None
        for res in race_results:
            if res["position"] == 1:
                winner_driver_id        = res["driverid"]
                winner_driver_name      = res["driverName"]
                winner_constructor_id   = res["constructorid"]
                winner_constructor_name = res["constructorName"]
                break

        # ── Derived: fastestLapMs + fastestLapTime per driver result
        for res in race_results:
            # Try both int and original type as key — guards against type
            # mismatch if psycopg2 returns driverid as a different int subtype
            did = res["driverid"]
            fl_ms = (
                fastest_laps_map.get((raceid, did)) or
                fastest_laps_map.get((int(raceid), int(did)))
            )
            res["fastestLapMs"] = safe_int(fl_ms)

            if fl_ms:
                total_s, ms_part = divmod(int(fl_ms), 1000)
                mins,    secs    = divmod(total_s, 60)
                res["fastestLapTime"] = f"{mins}:{secs:02d}.{ms_part:03d}"
            else:
                res["fastestLapTime"] = None

        # ── Build the race document
        doc = {
            "raceid":              raceid,
            "year":                r["year"],
            "round":               r["round"],
            "name":                r["name"],
            # date/time are VARCHAR in this schema — parse date to datetime,
            # keep time as plain string (e.g. "14:00:00")
            "date":                safe_date(r["date"]),
            "time":                r["time"],
            "circuit":             circuit_doc,   # EMBEDDED object
            "results":             race_results,  # EMBEDDED list
            # Derived fields
            "totalDrivers":            total_drivers,
            "winnerDriverId":          winner_driver_id,
            "winnerDriverName":        winner_driver_name,
            "winnerConstructorId":     winner_constructor_id,
            "winnerConstructorName":   winner_constructor_name,
        }

        ops.append(UpdateOne({"raceid": raceid}, {"$set": doc}, upsert=True))
        if len(ops) >= BATCH_SIZE:
            bulk_upsert(mongo_db.races, ops)
            ops = []

    bulk_upsert(mongo_db.races, ops)
    log.info("   ✓ %d races processed.", len(races))


# ──────────────────────────────────────────────────────────
# STEP 6 — indexes
# ──────────────────────────────────────────────────────────

def create_indexes(mongo_db):
    """
    Create indexes for races, drivers, and constructors.
    lap_times indexes are built inside migrate_lap_times() right after
    the bulk load for maximum performance.
    """
    log.info("── [6/6] Creating indexes …")

    mongo_db.races.create_index("raceid",               unique=True)
    mongo_db.races.create_index("year")
    mongo_db.races.create_index("winnerDriverId")
    mongo_db.races.create_index("circuit.circuitId")

    mongo_db.drivers.create_index("driverid",           unique=True)
    mongo_db.constructors.create_index("constructorid", unique=True)

    log.info("   ✓ All indexes created.")


# ──────────────────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────────────────

def main():
    start = datetime.now()
    log.info("=" * 60)
    log.info("  F1 Migration: PostgreSQL → MongoDB")
    log.info("=" * 60)

    # ── PostgreSQL connection
    log.info("Connecting to PostgreSQL …")
    pg_conn = psycopg2.connect(**PG_CONFIG)
    # RealDictCursor: rows behave like dicts → r["column_name"]
    pg_cur  = pg_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    log.info("   ✓ PostgreSQL connected.")

    # ── MongoDB connection
    log.info("Connecting to MongoDB …")
    mongo_client = MongoClient(MONGO_CONFIG["uri"])
    mongo_db     = mongo_client[MONGO_CONFIG["db"]]
    log.info("   ✓ MongoDB connected (db: %s).", MONGO_CONFIG["db"])

    try:
        # Steps 1-3: independent collections (no cross-dependencies)
        migrate_drivers(pg_cur, mongo_db)
        migrate_constructors(pg_cur, mongo_db)
        migrate_lap_times(pg_cur, mongo_db)

        # Step 4: bulk-load all lookup data before the races loop
        # (three queries total — avoids N+1 queries inside the loop)
        log.info("── [4/6] Pre-fetching circuits, results, fastest laps …")
        circuits_map     = fetch_circuits_map(pg_conn)
        results_map      = fetch_results_map(pg_conn)
        fastest_laps_map = fetch_fastest_laps_map(pg_conn)
        log.info(
            "   ✓ %d circuits | %d races with results | %d fastest-lap records.",
            len(circuits_map),
            len(results_map),
            len(fastest_laps_map),
        )

        # Step 5: main races collection with full embedding
        migrate_races(pg_cur, mongo_db, circuits_map, results_map, fastest_laps_map)

        # Step 6: indexes (lap_times indexes already done in step 3)
        create_indexes(mongo_db)

        elapsed = (datetime.now() - start).total_seconds()
        log.info("=" * 60)
        log.info("  ✅ Migration complete in %.1f s.", elapsed)
        log.info("=" * 60)

    except Exception as exc:
        log.exception("Migration failed: %s", exc)
        raise

    finally:
        pg_cur.close()
        pg_conn.close()
        mongo_client.close()
        log.info("Connections closed.")


if __name__ == "__main__":
    main()
