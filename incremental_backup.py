import psycopg2
from psycopg2 import sql
import json
import os
from datetime import datetime
import csv

# --- CONFIGURATION ---
DB_CONFIG = {
    "dbname": "your_db_name",
    "user": "your_user",
    "password": "your_password",
    "host": "localhost",
    "port": "5432"
}
BACKUP_DIR = "./backups_sql"
STATE_FILE = "backup_state.json"
TABLES_METADATA_CSV = "postgres_tables.csv"

# Priority for incremental tracking based on your schema
TRACKING_COLUMNS = ['last_modified_date', 'date_modified', 'updated_at', 'created_date', 'date_created', 'id']

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, 'r') as f:
            return json.load(f)
    return {}

def save_state(state):
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f, indent=4)

def format_sql_value(value):
    """Formats Python values into SQL-safe strings."""
    if value is None:
        return "NULL"
    if isinstance(value, (datetime, str)):
        return f"'{str(value).replace(\"'\", \"''\")}'"
    return str(value)

def run_sql_backup():
    if not os.path.exists(BACKUP_DIR):
        os.makedirs(BACKUP_DIR)

    state = load_state()
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()

    # Get unique tables from your provided metadata
    tables = []
    with open(TABLES_METADATA_CSV, 'r') as f:
        reader = csv.DictReader(f)
        tables = list(set(row['table_name'] for row in reader))

    for table in tables:
        # Determine tracking column
        cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = %s", (table,))
        cols = [c[0] for c in cur.fetchall()]
        track_col = next((c for c in TRACKING_COLUMNS if c in cols), None)

        if not track_col:
            continue

        last_val = state.get(table, "1900-01-01 00:00:00" if "date" in track_col else 0)

        # Fetch new data
        query = sql.SQL("SELECT * FROM {tbl} WHERE {col} > %s").format(
            tbl=sql.Identifier(table),
            col=sql.Identifier(track_col)
        )
        cur.execute(query, (last_val,))
        rows = cur.fetchall()

        if rows:
            col_names = [desc[0] for desc in cur.description]
            filename = f"{table}_inc_{datetime.now().strftime('%Y%m%d')}.sql"
            filepath = os.path.join(BACKUP_DIR, filename)

            with open(filepath, 'w') as f:
                f.write(f"-- Incremental backup for {table}\n")
                f.write(f"-- Generated: {datetime.now()}\n\n")
                
                for row in rows:
                    vals = ", ".join([format_sql_value(v) for v in row])
                    columns = ", ".join(col_names)
                    f.write(f"INSERT INTO {table} ({columns}) VALUES ({vals});\n")

            # Update state
            cur.execute(sql.SQL("SELECT MAX({col}) FROM {tbl}").format(
                tbl=sql.Identifier(table), col=sql.Identifier(track_col)
            ))
            new_max = cur.fetchone()[0]
            state[table] = str(new_max) if isinstance(new_max, datetime) else new_max
            print(f"[+] {table}: Created SQL dump with {len(rows)} inserts.")
        else:
            print(f"[-] {table}: No new data.")

    save_state(state)
    cur.close()
    conn.close()

if __name__ == "__main__":
    run_sql_backup()