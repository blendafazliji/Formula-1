"""

  F1 DATA VALIDATION  —  PostgreSQL  ↔  MongoDB

Runs AFTER migration.py and reports any drift between the
relational source and the NoSQL target.

Four validation layers
-----------------------
  1. Record counts per entity (handles MongoDB embedding)
  2. Checksums on the large tables
       - numeric aggregate checksums (sum of key columns)
       - an order-independent row hash on lap_times
  3. Spot-check queries run on BOTH databases and compared
  4. Derived-field checks (totalDrivers / winner / fastestLap)
       — proves the migration's TRANSFORMATIONS are correct

Output
------
  Console report + a `validation_report.log` file.
  Exit code 0 = all checks passed, 1 = at least one failed.

Schema reference (PostgreSQL — psycopg2 returns lowercase names)
  circuits    : circuitid, circuitref, name, country, lat, lng
  drivers     : driverid, driverref, forename, surname, nationality, dob
  constructors: constructorid, constructorref, name, nationality
  races       : raceid, year, round, circuitid, name, date, time
  results     : resultid, raceid, driverid, constructorid, grid, position, points
  lap_times   : raceid, driverid, lap, position, milliseconds

MongoDB target (db: f1_nosql)
  drivers      : driverid, driverref, forname, surname, nationality, dob
  constructors : constructorid, constructorref, name, nationality
  lap_times    : raceid, driverid, lap, position, milliseconds
  races        : raceid, year, round, name, date, time,
                 circuit { circuitId, circuitRef, name, country, lat, lng },
                 results [ { driverid, driverName, constructorid, constructorName,
                             grid, position, points, fastestLapMs, fastestLapTime } ],
                 totalDrivers, winnerDriverId, winnerConstructorId, ...

Dependencies:  pip install psycopg2-binary pymongo
Usage:         python dataValidation.py

"""

import os
import sys
import random
import hashlib
import logging

import psycopg2
import psycopg2.extras
from pymongo import MongoClient


# CONFIGURATION  (env vars override; defaults match migration.py)


PG_CONFIG = {
    "host":     os.getenv("PG_HOST", "localhost"),
    "port":     int(os.getenv("PG_PORT", "5432")),
    "dbname":   os.getenv("PG_DB",   "formula1"),
    "user":     os.getenv("PG_USER", "postgres"),
    "password": os.getenv("PG_PASSWORD", "password"),
}

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB  = os.getenv("MONGO_DB",  "f1_nosql")

DERIVED_SAMPLE_SIZE = 25      # number of random races checked in Layer 4
POINTS_TOLERANCE    = 0.01    # float comparison tolerance for points sums


# LOGGING  (console + file)

log = logging.getLogger("validation")
log.setLevel(logging.INFO)
_fmt = logging.Formatter("%(message)s")

_console = logging.StreamHandler(sys.stdout)
_console.setFormatter(_fmt)
log.addHandler(_console)

_file = logging.FileHandler("validation_report.log", mode="w", encoding="utf-8")
_file.setFormatter(_fmt)
log.addHandler(_file)


# CHECK TRACKING  (drives the final summary + exit code)

RESULTS = []   # list of (name, passed: bool)


def record(name, passed, detail=""):
    """Log one check result with a ✔ / ❌ marker and remember pass/fail."""
    mark = "✔" if passed else "❌"
    status = "PASS" if passed else "FAIL"
    line = f"{mark} {name}: {status}"
    if detail:
        line += f"  ({detail})"
    log.info(line)
    RESULTS.append((name, passed))


def section(title):
    log.info("\n=== " + title + " ===")

# COMPARISON HELPERS


def compare_dicts(name, pg_dict, mongo_dict, tol=0):
    """
    Compare two {key: number} maps. Records one overall PASS/FAIL plus a
    per-key breakdown. `tol` allows float wiggle room (used for points).
    """
    all_keys = set(pg_dict) | set(mongo_dict)
    mismatches = []

    for key in sorted(all_keys, key=lambda k: str(k)):
        pg_val = pg_dict.get(key, 0)
        mg_val = mongo_dict.get(key, 0)
        ok = abs(pg_val - mg_val) <= tol if tol else pg_val == mg_val
        if not ok:
            mismatches.append(f"{key}: PG={pg_val} MG={mg_val}")

    passed = not mismatches
    detail = f"{len(all_keys)} keys"
    if mismatches:
        detail = f"{len(mismatches)} mismatch(es): " + "; ".join(mismatches[:5])
        if len(mismatches) > 5:
            detail += f" … (+{len(mismatches) - 5} more)"
    record(name, passed, detail)


def pg_scalar(cur, sql, params=None):
    cur.execute(sql, params or ())
    row = cur.fetchone()
    return row[0] if row else None


# LAYER 1 — RECORD COUNTS PER ENTITY


def layer1_counts(pg_cur, mdb):
    section("LAYER 1 — RECORD COUNTS PER ENTITY")

    # --- Standalone collections: direct count comparison ---
    for table, coll in [("drivers", "drivers"),
                        ("constructors", "constructors"),
                        ("races", "races"),
                        ("lap_times", "lap_times")]:
        pg_n = pg_scalar(pg_cur, f"SELECT COUNT(*) FROM {table}")
        mg_n = mdb[coll].count_documents({})
        record(f"count[{table}]", pg_n == mg_n, f"PG={pg_n} MG={mg_n}")

    # --- results: embedded inside races → sum of array sizes ---
    pg_results = pg_scalar(pg_cur, "SELECT COUNT(*) FROM results")
    agg = list(mdb.races.aggregate([
        {"$group": {"_id": None, "n": {"$sum": {"$size": "$results"}}}}
    ]))
    mg_results = agg[0]["n"] if agg else 0
    record("count[results] (embedded)", pg_results == mg_results,
           f"PG={pg_results} MG(sum of arrays)={mg_results}")

    # --- circuits: embedded → compare DISTINCT circuit ids used in races ---
    # (apples-to-apples: a circuit with zero races can't appear embedded)
    pg_used = pg_scalar(pg_cur, "SELECT COUNT(DISTINCT circuitid) FROM races")
    mg_used = len(mdb.races.distinct("circuit.circuitId"))
    record("count[circuits] (distinct used)", pg_used == mg_used,
           f"PG={pg_used} MG={mg_used}")
    pg_total_circuits = pg_scalar(pg_cur, "SELECT COUNT(*) FROM circuits")
    log.info(f"  (info) total circuits in PG = {pg_total_circuits}; "
             f"unreferenced = {pg_total_circuits - pg_used}")


# LAYER 2 — CHECKSUMS ON THE LARGE TABLES


def layer2_checksums(pg_cur, mdb):
    section("LAYER 2 — CHECKSUMS (large tables)")

    # --- 2a. Numeric aggregate checksums on lap_times ---
    pg_cur.execute("""
        SELECT COALESCE(SUM(milliseconds), 0),
               COALESCE(SUM(lap), 0),
               COUNT(position)
        FROM lap_times
    """)
    pg_ms, pg_laps, pg_poscount = pg_cur.fetchone()

    agg = list(mdb.lap_times.aggregate([
        {"$group": {
            "_id": None,
            "ms":   {"$sum": "$milliseconds"},
            "laps": {"$sum": "$lap"},
            "poscount": {"$sum": {"$cond": [{"$ne": ["$position", None]}, 1, 0]}},
        }}
    ]))
    mg = agg[0] if agg else {"ms": 0, "laps": 0, "poscount": 0}

    record("lap_times SUM(milliseconds)", int(pg_ms) == int(mg["ms"]),
           f"PG={pg_ms} MG={mg['ms']}")
    record("lap_times SUM(lap)", int(pg_laps) == int(mg["laps"]),
           f"PG={pg_laps} MG={mg['laps']}")
    record("lap_times COUNT(position not null)",
           int(pg_poscount) == int(mg["poscount"]),
           f"PG={pg_poscount} MG={mg['poscount']}")

    # --- 2b. Numeric aggregate checksums on results ---
    pg_cur.execute("""
        SELECT COALESCE(SUM(points), 0), COALESCE(SUM(grid), 0)
        FROM results
    """)
    pg_points, pg_grid = pg_cur.fetchone()
    agg = list(mdb.races.aggregate([
        {"$unwind": "$results"},
        {"$group": {"_id": None,
                    "points": {"$sum": "$results.points"},
                    "grid":   {"$sum": "$results.grid"}}}
    ]))
    mg = agg[0] if agg else {"points": 0, "grid": 0}
    record("results SUM(points)",
           abs(float(pg_points) - float(mg["points"])) <= POINTS_TOLERANCE,
           f"PG={pg_points} MG={mg['points']}")
    record("results SUM(grid)", int(pg_grid) == int(mg["grid"]),
           f"PG={pg_grid} MG={mg['grid']}")

    # --- 2c. Order-independent row hash on lap_times ---------------------
    # Each row → canonical string → md5 → folded with XOR. Because the full
    # composite key is part of every string, no two rows hash alike, so XOR
    # is safe AND order-independent (no need for matching sort on both sides).
    def row_token(raceid, driverid, lap, position, milliseconds):
        pos = "" if position is None else str(position)
        ms = "" if milliseconds is None else str(milliseconds)
        s = f"{raceid}|{driverid}|{lap}|{pos}|{ms}"
        return int(hashlib.md5(s.encode()).hexdigest()[:16], 16)

    # PG side — server-side cursor so 589k rows never load into RAM at once
    pg_hash = 0
    with pg_cur.connection.cursor(name="lap_hash_cur") as ss:
        ss.itersize = 50_000
        ss.execute("SELECT raceid, driverid, lap, position, milliseconds FROM lap_times")
        for r in ss:
            pg_hash ^= row_token(*r)

    # Mongo side — project only needed fields, no _id
    mg_hash = 0
    cursor = mdb.lap_times.find(
        {}, {"_id": 0, "raceid": 1, "driverid": 1, "lap": 1,
             "position": 1, "milliseconds": 1}
    )
    for d in cursor:
        mg_hash ^= row_token(d.get("raceid"), d.get("driverid"), d.get("lap"),
                             d.get("position"), d.get("milliseconds"))

    record("lap_times row-hash (XOR-fold md5)", pg_hash == mg_hash,
           f"PG={pg_hash:016x} MG={mg_hash:016x}")


# LAYER 3 — SPOT-CHECK QUERIES (run on both, compared)


def layer3_spot_checks(pg_cur, mdb):
    section("LAYER 3 — SPOT-CHECK QUERIES")

    # --- Driver wins (keyed by driverid, full map — no LIMIT tie issues) ---
    pg_cur.execute("""
        SELECT driverid, COUNT(*) FROM results
        WHERE position = 1 GROUP BY driverid
    """)
    pg_wins = {r[0]: r[1] for r in pg_cur.fetchall()}
    mg_wins = {d["_id"]: d["wins"] for d in mdb.races.aggregate([
        {"$unwind": "$results"},
        {"$match": {"results.position": 1}},
        {"$group": {"_id": "$results.driverid", "wins": {"$sum": 1}}},
    ])}
    compare_dicts("spot: driver wins", pg_wins, mg_wins)

    # --- Constructor wins ---
    pg_cur.execute("""
        SELECT constructorid, COUNT(*) FROM results
        WHERE position = 1 GROUP BY constructorid
    """)
    pg_cw = {r[0]: r[1] for r in pg_cur.fetchall()}
    mg_cw = {d["_id"]: d["wins"] for d in mdb.races.aggregate([
        {"$unwind": "$results"},
        {"$match": {"results.position": 1}},
        {"$group": {"_id": "$results.constructorid", "wins": {"$sum": 1}}},
    ])}
    compare_dicts("spot: constructor wins", pg_cw, mg_cw)

    # --- Total points per constructor (float → tolerance) ---
    pg_cur.execute("""
        SELECT constructorid, COALESCE(SUM(points), 0) FROM results
        GROUP BY constructorid
    """)
    pg_pts = {r[0]: round(float(r[1]), 2) for r in pg_cur.fetchall()}
    mg_pts = {d["_id"]: round(float(d["pts"]), 2) for d in mdb.races.aggregate([
        {"$unwind": "$results"},
        {"$group": {"_id": "$results.constructorid",
                    "pts": {"$sum": "$results.points"}}},
    ])}
    compare_dicts("spot: points per constructor", pg_pts, mg_pts,
                  tol=POINTS_TOLERANCE)

    # --- Races per circuit ---
    pg_cur.execute("SELECT circuitid, COUNT(*) FROM races GROUP BY circuitid")
    pg_rc = {r[0]: r[1] for r in pg_cur.fetchall()}
    mg_rc = {d["_id"]: d["n"] for d in mdb.races.aggregate([
        {"$group": {"_id": "$circuit.circuitId", "n": {"$sum": 1}}},
    ])}
    compare_dicts("spot: races per circuit", pg_rc, mg_rc)



# LAYER 4 — DERIVED-FIELD CHECKS  (validates the TRANSFORMATIONS)


def layer4_derived(pg_cur, mdb):
    section(f"LAYER 4 — DERIVED FIELDS (sample of {DERIVED_SAMPLE_SIZE} races)")

    all_ids = mdb.races.distinct("raceid")
    if not all_ids:
        record("derived fields", False, "no races in MongoDB")
        return
    sample = random.sample(all_ids, min(DERIVED_SAMPLE_SIZE, len(all_ids)))

    bad_total, bad_winner, bad_fastest = [], [], []

    for rid in sample:
        doc = mdb.races.find_one({"raceid": rid})
        if not doc:
            continue

        # totalDrivers == PG count of results for this race
        pg_n = pg_scalar(pg_cur,
                         "SELECT COUNT(*) FROM results WHERE raceid = %s", (rid,))
        if doc.get("totalDrivers") != pg_n:
            bad_total.append(f"race {rid}: MG={doc.get('totalDrivers')} PG={pg_n}")

        # winnerDriverId == PG driver with position = 1 (None if none)
        pg_winner = pg_scalar(pg_cur,
                              "SELECT driverid FROM results "
                              "WHERE raceid = %s AND position = 1 LIMIT 1", (rid,))
        if doc.get("winnerDriverId") != pg_winner:
            bad_winner.append(
                f"race {rid}: MG={doc.get('winnerDriverId')} PG={pg_winner}")

        # fastestLapMs per embedded result == PG MIN(milliseconds) for that driver
        pg_cur.execute("""
            SELECT driverid, MIN(milliseconds) FROM lap_times
            WHERE raceid = %s GROUP BY driverid
        """, (rid,))
        pg_fast = {r[0]: r[1] for r in pg_cur.fetchall()}
        for res in doc.get("results", []):
            did = res.get("driverid")
            expected = pg_fast.get(did)            # None if driver had no laps
            if res.get("fastestLapMs") != expected:
                bad_fastest.append(
                    f"race {rid} driver {did}: "
                    f"MG={res.get('fastestLapMs')} PG={expected}")

    record("derived: totalDrivers", not bad_total,
           "all match" if not bad_total else "; ".join(bad_total[:5]))
    record("derived: winnerDriverId", not bad_winner,
           "all match" if not bad_winner else "; ".join(bad_winner[:5]))
    record("derived: fastestLapMs", not bad_fastest,
           "all match" if not bad_fastest else "; ".join(bad_fastest[:5]))



# MAIN

def main():
    log.info("=" * 60)
    log.info("   F1 DATA VALIDATION  (PostgreSQL  ↔  MongoDB)")
    log.info("=" * 60)

    pg_conn = psycopg2.connect(**PG_CONFIG)
    pg_cur = pg_conn.cursor()
    mongo_client = MongoClient(MONGO_URI)
    mdb = mongo_client[MONGO_DB]

    try:
        layer1_counts(pg_cur, mdb)
        layer2_checksums(pg_cur, mdb)
        layer3_spot_checks(pg_cur, mdb)
        layer4_derived(pg_cur, mdb)
    finally:
        pg_cur.close()
        pg_conn.close()
        mongo_client.close()

    passed = sum(1 for _, ok in RESULTS if ok)
    total = len(RESULTS)
    log.info("\n" + "=" * 60)
    log.info(f"   VALIDATION COMPLETE — {passed}/{total} checks passed")
    if passed != total:
        failed = [n for n, ok in RESULTS if not ok]
        log.info("   FAILED: " + ", ".join(failed))
    log.info("=" * 60 + "\n")

    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
