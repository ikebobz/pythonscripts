"""
Incremental backup of a PostgreSQL (LAMISPlus) database.

Every table listed in data_tables.csv is backed up with one of four modes:

  watermark  Table has a timestamp "last modified" column and a primary key.
             Rows whose modified time is after the previous run are exported.
  hash       Table has a primary key but no usable timestamp column.
             A fingerprint (md5) of every row is compared with the one saved
             by the previous run; new/changed rows are exported, and rows that
             disappeared are exported as deletes.
  full       Table has no primary key. If its content changed since the
             previous run, the whole table is exported as a snapshot.
  skip       Never backed up (set in table_modes.csv).

The mode is chosen automatically ("auto") unless table_modes.csv says otherwise.

Output: <output>/YYYY-MM-DD_HHMMSS.zip containing <schema>/<table>.jsonl files
and run_info.json. Each jsonl line is one record:
  {"schema_name", "table_name", "op", "pk", "modified_at", "row_data"}
  op = "U" upsert row, "D" delete row by pk, "S" part of a full-table snapshot
       (row_data null in an "S" record means the table is now empty).

Checkpoints: <output>/manifest.json (watermark time) and <output>/state.db
(row fingerprints). Losing state.db is safe: the next run re-exports the
hash/full tables in full.
"""
import argparse
import base64
import csv
import json
import logging
import os
import shutil
import sqlite3
import sys
import uuid
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor

log = logging.getLogger("backup")

# Columns that can serve as "last modified" markers, in order of preference.
WATERMARK_COLUMNS = ["date_modified", "last_modified_date", "updated_at",
                     "last_updated", "date_updated", "updated"]
# Only real timestamps are reliable; a plain DATE column loses edits made later
# on the same day as a backup, so such tables fall back to hash mode.
WATERMARK_TYPES = {"timestamp without time zone", "timestamp with time zone"}
VALID_MODES = {"auto", "watermark", "hash", "full", "skip"}
FETCH_CHUNK = 1000          # primary keys per "fetch changed rows" query
SCAN_BATCH = 20000          # rows per round trip when scanning fingerprints


# ---------------------------------------------------------------- helpers
def qi(name):
    """Quote a SQL identifier."""
    return '"' + str(name).replace('"', '""') + '"'


def iso_now():
    return datetime.now(timezone.utc).isoformat()


def parse_iso(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def to_jsonable(v):
    """Convert a value from psycopg2 into something json.dumps can store losslessly."""
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    if isinstance(v, (bytes, bytearray, memoryview)):
        return {"$bytea": base64.b64encode(bytes(v)).decode("ascii")}
    if isinstance(v, (datetime, date, time)):
        return v.isoformat()
    if isinstance(v, timedelta):
        return f"{v.total_seconds()} seconds"
    if isinstance(v, (Decimal, uuid.UUID)):
        return str(v)
    if isinstance(v, dict):
        return {k: to_jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [to_jsonable(x) for x in v]
    return str(v)


def pk_key(values):
    """Stable text key for a primary-key tuple (used in state.db)."""
    return json.dumps([to_jsonable(v) for v in values], separators=(",", ":"))


# ---------------------------------------------------------------- config files
def load_table_list(csv_path):
    """Unique (schema, table) pairs from data_tables.csv, in file order."""
    seen, tables = set(), []
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fields = {(c or "").strip().lower() for c in reader.fieldnames or []}
        missing = {"schema_name", "table_name"} - fields
        if missing:
            raise ValueError(f"Missing columns in {csv_path}: {sorted(missing)}")
        for row in reader:
            row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
            key = (row["schema_name"], row["table_name"])
            if key[0] and key[1] and key not in seen:
                seen.add(key)
                tables.append(key)
    return tables


def load_table_modes(path):
    """Optional overrides: schema_name,table_name,mode[,note]."""
    modes = {}
    if not path or not Path(path).exists():
        return modes
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
            if not row.get("table_name") or row.get("schema_name", "").startswith("#"):
                continue
            mode = row.get("mode", "auto").lower() or "auto"
            if mode not in VALID_MODES:
                raise ValueError(f"{path}: invalid mode '{mode}' for {row['table_name']} "
                                 f"(use one of {sorted(VALID_MODES)})")
            modes[(row.get("schema_name") or "public", row["table_name"])] = mode
    return modes


def load_manifest(manifest_path, fallback_last_run=None):
    p = Path(manifest_path)
    if not p.exists():
        if not fallback_last_run:
            raise ValueError("manifest.json not found and --last-run not provided")
        return {"generated_at": iso_now(), "last_run": fallback_last_run, "archives": []}
    with p.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_manifest(manifest_path, manifest):
    tmp = Path(str(manifest_path) + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, default=str)
    os.replace(tmp, manifest_path)


# ---------------------------------------------------------------- state.db
class StateStore:
    """Row and table fingerprints from the previous successful run."""

    def __init__(self, path):
        self.db = sqlite3.connect(str(path))
        self.db.execute("CREATE TABLE IF NOT EXISTS row_hash ("
                        "tbl TEXT NOT NULL, pk TEXT NOT NULL, h TEXT NOT NULL, "
                        "PRIMARY KEY (tbl, pk)) WITHOUT ROWID")
        self.db.execute("CREATE TABLE IF NOT EXISTS table_hash ("
                        "tbl TEXT PRIMARY KEY, h TEXT NOT NULL)")
        self.db.commit()
        # Pending changes, written only after the archive is safely published.
        self.pending_upserts = []   # (tbl, pk, h)
        self.pending_deletes = []   # (tbl, pk)
        self.pending_tables = []    # (tbl, h)

    def row_hashes(self, tbl):
        return dict(self.db.execute("SELECT pk, h FROM row_hash WHERE tbl = ?", (tbl,)))

    def table_hash(self, tbl):
        r = self.db.execute("SELECT h FROM table_hash WHERE tbl = ?", (tbl,)).fetchone()
        return r[0] if r else None

    def commit(self):
        with self.db:
            self.db.executemany("INSERT OR REPLACE INTO row_hash (tbl, pk, h) VALUES (?, ?, ?)",
                                self.pending_upserts)
            self.db.executemany("DELETE FROM row_hash WHERE tbl = ? AND pk = ?",
                                self.pending_deletes)
            self.db.executemany("INSERT OR REPLACE INTO table_hash (tbl, h) VALUES (?, ?)",
                                self.pending_tables)

    def close(self):
        self.db.close()


# ---------------------------------------------------------------- database
def table_exists(conn, schema, table):
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass(%s) IS NOT NULL", (f"{qi(schema)}.{qi(table)}",))
        return cur.fetchone()[0]


def get_columns(conn, schema, table):
    with conn.cursor() as cur:
        cur.execute("SELECT column_name, data_type FROM information_schema.columns "
                    "WHERE table_schema = %s AND table_name = %s ORDER BY ordinal_position",
                    (schema, table))
        return {name: dtype for name, dtype in cur.fetchall()}


def get_primary_key(conn, schema, table):
    sql = """
    SELECT a.attname
    FROM pg_index i
    JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
    JOIN pg_class c ON c.oid = i.indrelid
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE i.indisprimary AND n.nspname = %s AND c.relname = %s
    ORDER BY array_position(i.indkey, a.attnum)
    """
    with conn.cursor() as cur:
        cur.execute(sql, (schema, table))
        return [r[0] for r in cur.fetchall()]


def resolve_mode(requested, columns, pk_cols):
    """Return (mode, watermark_column, reason)."""
    if requested == "skip":
        return "skip", None, "listed as skip in table_modes.csv"
    wm = next((c for c in WATERMARK_COLUMNS if columns.get(c) in WATERMARK_TYPES), None)
    if requested in ("auto", "watermark"):
        if wm and pk_cols:
            return "watermark", wm, f"timestamp column {wm}"
        if requested == "watermark":
            log.warning("  watermark mode requested but table has no timestamp "
                        "modified column and primary key; falling back")
    if requested in ("auto", "watermark", "hash") and pk_cols:
        why = "no timestamp modified column"
        date_wm = next((c for c in WATERMARK_COLUMNS if c in columns), None)
        if date_wm:
            why = f"{date_wm} is {columns[date_wm]}, not a timestamp"
        if requested == "hash":
            why = "set to hash in table_modes.csv"
        return "hash", None, why
    return "full", None, "no primary key" if not pk_cols else "set to full in table_modes.csv"


def record(schema, table, op, pk, modified_at, row):
    return json.dumps({
        "schema_name": schema,
        "table_name": table,
        "op": op,
        "pk": pk,
        "modified_at": modified_at,
        "row_data": to_jsonable(row) if row is not None else None,
    }, separators=(",", ":")) + "\n"


def backup_watermark(conn, schema, table, wm, pk_cols, last_run_dt, out):
    sql = (f"SELECT * FROM {qi(schema)}.{qi(table)} "
           f"WHERE {qi(wm)} > %s ORDER BY {qi(wm)} ASC")
    n = 0
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(sql, (last_run_dt,))
        while True:
            rows = cur.fetchmany(SCAN_BATCH)
            if not rows:
                break
            for row in rows:
                pk = {k: to_jsonable(row[k]) for k in pk_cols}
                out.write(record(schema, table, "U", pk, to_jsonable(row.get(wm)), row))
                n += 1
    return n, 0


def backup_hash(conn, schema, table, pk_cols, state, out):
    tbl = f"{schema}.{table}"
    qt = f"{qi(schema)}.{qi(table)}"
    pk_sel = ", ".join(f"__r.{qi(c)}" for c in pk_cols)

    # 1. Fingerprint every row (only keys + 32-char hashes cross the network).
    current = {}
    with conn.cursor() as cur:
        cur.execute(f"SELECT {pk_sel}, md5(__r::text) FROM {qt} AS __r")
        while True:
            rows = cur.fetchmany(SCAN_BATCH)
            if not rows:
                break
            for r in rows:
                current[pk_key(r[:-1])] = (tuple(r[:-1]), r[-1])

    previous = state.row_hashes(tbl)
    changed = [vals for k, (vals, h) in current.items() if previous.get(k) != h]
    deleted = [k for k in previous if k not in current]

    # 2. Fetch full rows for new/changed keys only. The hash stored is the one
    #    of the row actually exported, so a row edited mid-run is caught next time.
    n_changed = 0
    if len(pk_cols) == 1:
        where = f"{qi(pk_cols[0])} IN %s"
    else:
        where = "(" + ", ".join(qi(c) for c in pk_cols) + ") IN %s"
    sql = f'SELECT __r.*, md5(__r::text) AS "__row_md5__" FROM {qt} AS __r WHERE {where}'
    for i in range(0, len(changed), FETCH_CHUNK):
        chunk = changed[i:i + FETCH_CHUNK]
        param = tuple(v[0] for v in chunk) if len(pk_cols) == 1 else tuple(chunk)
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, (param,))
            for row in cur.fetchall():
                h = row.pop("__row_md5__")
                vals = [row[c] for c in pk_cols]
                pk = {c: to_jsonable(row[c]) for c in pk_cols}
                out.write(record(schema, table, "U", pk, None, row))
                state.pending_upserts.append((tbl, pk_key(vals), h))
                n_changed += 1

    # 3. Rows that no longer exist.
    for k in deleted:
        pk = dict(zip(pk_cols, json.loads(k)))
        out.write(record(schema, table, "D", pk, None, None))
        state.pending_deletes.append((tbl, k))

    return n_changed, len(deleted)


def backup_full(conn, schema, table, state, out):
    tbl = f"{schema}.{table}"
    qt = f"{qi(schema)}.{qi(table)}"
    with conn.cursor() as cur:
        cur.execute(f"SELECT md5(coalesce(string_agg(md5(__r::text), ',' "
                    f"ORDER BY md5(__r::text)), '')), count(*) FROM {qt} AS __r")
        digest, count = cur.fetchone()
    if digest == state.table_hash(tbl):
        return 0, 0
    n = 0
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(f"SELECT * FROM {qt}")
        while True:
            rows = cur.fetchmany(SCAN_BATCH)
            if not rows:
                break
            for row in rows:
                out.write(record(schema, table, "S", None, None, row))
                n += 1
    if n == 0:
        out.write(record(schema, table, "S", None, None, None))   # table is now empty
    state.pending_tables.append((tbl, digest))
    return n, 0


# ---------------------------------------------------------------- main
def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--csv", required=True, help="data_tables.csv (tables to back up)")
    parser.add_argument("--modes", default=None,
                        help="table_modes.csv overrides (default: next to --csv)")
    parser.add_argument("--output", required=True)
    parser.add_argument("--last-run", default=None)
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", default=5432, type=int)
    parser.add_argument("--dbname", required=True)
    parser.add_argument("--user", required=True)
    parser.add_argument("--password", default=os.environ.get("PGPASSWORD"))
    args = parser.parse_args()
    if not args.password:
        parser.error("--password not given and PGPASSWORD is not set")

    logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                        format="%(asctime)s %(levelname)s %(message)s")

    # Watermark captured BEFORE querying so rows changed during the run are not skipped.
    run_started_at = iso_now()

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    staging = output_dir / "_staging"           # never watched by the shipper
    manifest_path = output_dir / "manifest.json"
    manifest = load_manifest(manifest_path, fallback_last_run=args.last_run)
    last_run_dt = parse_iso(manifest["last_run"])

    run_name = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")
    n = 1
    while (output_dir / f"{run_name}.zip").exists():   # two runs in the same second
        n += 1
        run_name = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S") + f"_{n}"
    shutil.rmtree(staging, ignore_errors=True)
    run_dir = staging / run_name
    run_dir.mkdir(parents=True)

    tables = load_table_list(args.csv)
    modes_path = args.modes or str(Path(args.csv).with_name("table_modes.csv"))
    overrides = load_table_modes(modes_path)
    for key in overrides:
        if key not in set(tables):
            log.warning("table_modes.csv lists %s.%s, which is not in %s", *key, args.csv)

    log.info("Backup run %s: %d tables, changes since %s", run_name, len(tables), last_run_dt)
    state = StateStore(output_dir / "state.db")
    conn = psycopg2.connect(host=args.host, port=args.port, dbname=args.dbname,
                            user=args.user, password=args.password)
    # One consistent, read-only snapshot of the whole database for this run.
    conn.set_session(isolation_level="REPEATABLE READ", readonly=True)

    entries, totals = [], {}
    try:
        for schema, table in tables:
            requested = overrides.get((schema, table), "auto")
            if not table_exists(conn, schema, table):
                mode, wm, why, pk_cols = "missing", None, "table not found in database", []
            else:
                columns = get_columns(conn, schema, table)
                pk_cols = get_primary_key(conn, schema, table)
                mode, wm, why = resolve_mode(requested, columns, pk_cols)

            changed = deleted = 0
            if mode not in ("skip", "missing"):
                out_file = run_dir / schema / f"{table}.jsonl"
                out_file.parent.mkdir(parents=True, exist_ok=True)
                with open(out_file, "w", encoding="utf-8") as out:
                    if mode == "watermark":
                        changed, deleted = backup_watermark(conn, schema, table, wm, pk_cols,
                                                            last_run_dt, out)
                    elif mode == "hash":
                        changed, deleted = backup_hash(conn, schema, table, pk_cols, state, out)
                    else:
                        changed, deleted = backup_full(conn, schema, table, state, out)
                if out_file.stat().st_size == 0:
                    out_file.unlink()

            totals[mode] = totals.get(mode, 0) + 1
            log.info("%-10s %-55s changed=%-7d deleted=%-6d (%s)",
                     mode, f"{schema}.{table}", changed, deleted, why)
            entries.append({
                "schema_name": schema, "table_name": table, "mode": mode,
                "reason": why, "watermark_column": wm, "primary_key": pk_cols,
                "row_count": changed, "deleted_count": deleted,
            })
        conn.rollback()   # end the read-only snapshot

        run_info = {"run": run_name, "started_at": run_started_at,
                    "changes_since": manifest["last_run"], "tables": entries}
        with open(run_dir / "run_info.json", "w", encoding="utf-8") as f:
            json.dump(run_info, f, indent=2)

        # Build the zip in _staging, then move it into place in one step so the
        # shipper never sees a half-written archive.
        tmp_zip = shutil.make_archive(str(staging / run_name), "zip",
                                      root_dir=staging, base_dir=run_name)
        zip_path = output_dir / f"{run_name}.zip"
        os.replace(tmp_zip, zip_path)
        shutil.rmtree(staging, ignore_errors=True)

        state.commit()
        manifest["generated_at"] = iso_now()
        manifest["last_run"] = run_started_at
        manifest.setdefault("archives", []).append({
            "run_date": run_name[:10],
            "zip_file": str(zip_path),
            "tables": [e for e in entries if e["row_count"] or e["deleted_count"]],
        })
        save_manifest(manifest_path, manifest)

        summary = ", ".join(f"{k}={v}" for k, v in sorted(totals.items()))
        log.info("Summary: %s", summary)
        log.info("Rows exported: %d, deletes: %d -> %s",
                 sum(e["row_count"] for e in entries),
                 sum(e["deleted_count"] for e in entries), zip_path)
        if totals.get("missing"):
            log.warning("%d table(s) in %s were not found in the database: %s",
                        totals["missing"], args.csv,
                        ", ".join(e["table_name"] for e in entries if e["mode"] == "missing"))
    finally:
        conn.close()
        state.close()
        shutil.rmtree(staging, ignore_errors=True)


if __name__ == "__main__":
    main()
