# ===============================
# F1 DATA VALIDATION SCRIPT
# PostgreSQL ↔ MongoDB (with embedded support)
# ===============================


# ===============================
# HELPERS
# ===============================

def normalize_pg(rows, key_field, value_field):
    return {row[key_field]: row[value_field] for row in rows}


def normalize_mongo(rows, key_field="_id", value_field="wins"):
    return {row[key_field]: row[value_field] for row in rows}


def compare_dicts(pg_dict, mongo_dict):
    all_keys = set(pg_dict.keys()) | set(mongo_dict.keys())

    for key in sorted(all_keys):
        pg_val = pg_dict.get(key, 0)
        mg_val = mongo_dict.get(key, 0)

        if pg_val == mg_val:
            print(f"✔ {key}: PASS ({pg_val})")
        else:
            print(f"❌ {key}: FAIL (PG={pg_val}, MG={mg_val})")


# ===============================
# OUTPUT FORMATTING
# ===============================

def print_header():
    print("\n" + "=" * 60)
    print("   F1 DATA VALIDATION (PostgreSQL ↔ MongoDB)")
    print("=" * 60 + "\n")


def print_section(title):
    print("\n=== " + title + " ===")


# ===============================
# RECORD COUNT VALIDATION
# ===============================

def validate_counts(pg_counts, mongo_counts, embedded_tables=None):

    print_section("RECORD COUNT VALIDATION")

    embedded_tables = embedded_tables or []

    all_tables = set(pg_counts.keys()) | set(mongo_counts.keys())

    for table in sorted(all_tables):

        pg_val = pg_counts.get(table, 0)
        mg_val = mongo_counts.get(table, 0)

        # 🔥 IMPORTANT: embedded collections handling
        if table in embedded_tables:
            print(f"⚠ {table}: EMBEDDED IN MONGODB (PG={pg_val}, MG={mg_val})")
            continue

        if pg_val == mg_val:
            print(f"✔ {table}: PASS ({pg_val})")
        else:
            print(f"❌ {table}: FAIL (PG={pg_val}, MG={mg_val})")


# ===============================
# DRIVER WINS VALIDATION
# ===============================

def validate_top_driver_wins(pg_data, mongo_data):

    print_section("CHECKSUM: Top Driver Wins")

    pg_dict = normalize_pg(pg_data, "driver", "wins")
    mongo_dict = normalize_mongo(mongo_data)

    compare_dicts(pg_dict, mongo_dict)


# ===============================
# CONSTRUCTOR WINS VALIDATION
# ===============================

def validate_top_constructor_wins(pg_data, mongo_data):

    print_section("SPOT CHECK: Top Constructor Wins")

    pg_dict = normalize_pg(pg_data, "constructor", "wins")
    mongo_dict = normalize_mongo(mongo_data)

    compare_dicts(pg_dict, mongo_dict)


# ===============================
# MAIN RUNNER
# ===============================

def run_validation(
        pg_counts,
        mongo_counts,
        pg_driver_wins,
        mongo_driver_wins,
        pg_constructor_wins,
        mongo_constructor_wins
):

    print_header()

    # 🔥 embedded collections declared here
    embedded_tables = ["circuits", "results"]

    validate_counts(pg_counts, mongo_counts, embedded_tables)

    validate_top_driver_wins(pg_driver_wins, mongo_driver_wins)

    validate_top_constructor_wins(pg_constructor_wins, mongo_constructor_wins)

    print("\n" + "=" * 60)
    print("VALIDATION COMPLETE")
    print("=" * 60 + "\n")


# ===============================
# TEST DATA (REMOVE IN REAL RUN)
# ===============================

if __name__ == "__main__":

    pg_counts = {
        "drivers": 861,
        "constructors": 212,
        "races": 1125,
        "lap_times": 589081,
        "circuits": 77,
        "results": 26759
    }

    mongo_counts = {
        "drivers": 861,
        "constructors": 212,
        "races": 1125,
        "lap_times": 589081,
        "circuits": 0,   # embedded
        "results": 0     # embedded
    }

    pg_driver_wins = [
        {"driver": "Hamilton", "wins": 103}
    ]

    mongo_driver_wins = [
        {"_id": "Hamilton", "wins": 103}
    ]

    pg_constructor_wins = [
        {"constructor": "Ferrari", "wins": 249}
    ]

    mongo_constructor_wins = [
        {"_id": "Ferrari", "wins": 249}
    ]

    run_validation(
        pg_counts,
        mongo_counts,
        pg_driver_wins,
        mongo_driver_wins,
        pg_constructor_wins,
        mongo_constructor_wins
    )
