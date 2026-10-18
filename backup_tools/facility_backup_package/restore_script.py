"""
Restore backup archives produced by backup_script.py into a PostgreSQL database.

--input can be:
  * a folder of backup .zip files (applied oldest first, by file name), or
  * a single .zip file, or
  * a folder of already-extracted backups (the old way).

Record types:
  U  insert the row, or update it if the primary key already exists
  D  delete the row with that primary key
  S  full-table snapshot: the table is emptied, then the rows are inserted

Everything is applied in ONE transaction: if anything fails, nothing changes.
By default foreign-key checks and triggers are switched off for the restore
session (session_replication_role = replica) so tables can be loaded in any
order; this needs a superuser such as "postgres". Use --keep-triggers to disable
that behaviour.
"""
import argparse
import base64
import getpass
import io
import json
import logging
import os
import sys
import zipfile
from pathlib import Path

import psycopg2
from psycopg2.extras import Json

log = logging.getLogger("restore")


def qi(name):
    return '"' + str(name).replace('"', '""') + '"'


class Restorer:
    def __init__(self, conn):
        self.conn = conn
        self.columns = {}           # (schema, table) -> {column: data_type}
        self.warned = set()
        self.counts = {"U": 0, "D": 0, "S": 0}

    def table_columns(self, schema, table):
        key = (schema, table)
        if key not in self.columns:
            with self.conn.cursor() as cur:
                cur.execute("SELECT column_name, data_type FROM information_schema.columns "
                            "WHERE table_schema = %s AND table_name = %s", (schema, table))
                self.columns[key] = dict(cur.fetchall())
        return self.columns[key]

    def adapt(self, dtype, value):
        """Turn a JSON value back into something psycopg2 can send for that column."""
        if value is None:
            return None
        if dtype in ("json", "jsonb"):
            return Json(value)
        if dtype == "bytea":
            if isinstance(value, dict) and "$bytea" in value:
                return psycopg2.Binary(base64.b64decode(value["$bytea"]))
            return value
        return value

    def prepared_row(self, schema, table, row):
        cols = self.table_columns(schema, table)
        if not cols:
            raise RuntimeError(f"Table {schema}.{table} does not exist in the target database")
        out = {}
        for c, v in row.items():
            if c not in cols:
                if (schema, table, c) not in self.warned:
                    self.warned.add((schema, table, c))
                    log.warning("Column %s.%s.%s not in target database; ignored", schema, table, c)
                continue
            out[c] = self.adapt(cols[c], v)
        return out

    def upsert(self, schema, table, pk_cols, row):
        row = self.prepared_row(schema, table, row)
        cols = list(row.keys())
        qt = f"{qi(schema)}.{qi(table)}"
        insert_cols = ", ".join(qi(c) for c in cols)
        placeholders = ", ".join(["%s"] * len(cols))
        if pk_cols:
            target = ", ".join(qi(c) for c in pk_cols)
            update_cols = [c for c in cols if c not in pk_cols]
            if update_cols:
                sets = ", ".join(f"{qi(c)} = EXCLUDED.{qi(c)}" for c in update_cols)
                tail = f"ON CONFLICT ({target}) DO UPDATE SET {sets}"
            else:
                tail = f"ON CONFLICT ({target}) DO NOTHING"
        else:
            tail = ""
        with self.conn.cursor() as cur:
            cur.execute(f"INSERT INTO {qt} ({insert_cols}) VALUES ({placeholders}) {tail}",
                        list(row.values()))

    def delete(self, schema, table, pk):
        cols = self.table_columns(schema, table)
        where = " AND ".join(f"{qi(c)} = %s" for c in pk)
        values = [self.adapt(cols.get(c), v) for c, v in pk.items()]
        with self.conn.cursor() as cur:
            cur.execute(f"DELETE FROM {qi(schema)}.{qi(table)} WHERE {where}", values)

    def apply_lines(self, lines, label):
        cleared = set()   # tables already emptied for a snapshot in this file
        n = 0
        for line in lines:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            schema, table, op = rec["schema_name"], rec["table_name"], rec.get("op", "U")
            if op == "U":
                self.upsert(schema, table, list((rec.get("pk") or {}).keys()), rec["row_data"])
            elif op == "D":
                self.delete(schema, table, rec["pk"])
            elif op == "S":
                if (schema, table) not in cleared:
                    with self.conn.cursor() as cur:
                        cur.execute(f"DELETE FROM {qi(schema)}.{qi(table)}")
                    cleared.add((schema, table))
                if rec.get("row_data") is not None:
                    self.upsert(schema, table, [], rec["row_data"])
            else:
                raise ValueError(f"{label}: unknown op {op!r}")
            self.counts[op] = self.counts.get(op, 0) + 1
            n += 1
        log.info("  %-60s %d records", label, n)


def sources(input_path):
    """Yield (label, iterator of lines) in the order they must be applied."""
    p = Path(input_path)
    if p.is_file() and p.suffix.lower() == ".zip":
        zips = [p]
    elif p.is_dir():
        zips = sorted(p.glob("*.zip"), key=lambda z: z.name)
    else:
        raise SystemExit(f"--input not found: {input_path}")

    if zips:
        for z in zips:
            log.info("Archive %s", z.name)
            with zipfile.ZipFile(z) as zf:
                for name in sorted(n for n in zf.namelist() if n.endswith(".jsonl")):
                    with zf.open(name) as raw:
                        yield f"{z.name}:{name}", io.TextIOWrapper(raw, encoding="utf-8")
    else:
        # Extracted folders: names start with the run timestamp, so path order = time order.
        for f in sorted(p.rglob("*.jsonl"), key=lambda x: x.relative_to(p).as_posix()):
            with open(f, encoding="utf-8") as fh:
                yield f.relative_to(p).as_posix(), fh


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input", "--input-dir", dest="input", required=True,
                        help="folder of backup zips, a single zip, or an extracted folder")
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", default=5432, type=int)
    parser.add_argument("--dbname", required=True)
    parser.add_argument("--user", required=True)
    parser.add_argument("--password", default=os.environ.get("PGPASSWORD"),
                        help="defaults to the PGPASSWORD environment variable, else prompts")
    parser.add_argument("--keep-triggers", action="store_true",
                        help="leave foreign-key checks and triggers on during the restore")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                        format="%(asctime)s %(levelname)s %(message)s")
    password = args.password or getpass.getpass(f"Database password for {args.user}: ")

    conn = psycopg2.connect(host=args.host, port=args.port, dbname=args.dbname,
                            user=args.user, password=password)
    conn.autocommit = False
    restorer = Restorer(conn)
    try:
        if not args.keep_triggers:
            try:
                with conn.cursor() as cur:
                    cur.execute("SET session_replication_role = replica")
            except psycopg2.Error as e:
                conn.rollback()
                log.warning("Could not switch off foreign-key checks (%s). Continuing with "
                            "them on; restore may fail if tables load out of order.",
                            str(e).strip())
        for label, lines in sources(args.input):
            restorer.apply_lines(lines, label)
        conn.commit()
        log.info("Restore committed: %d upserts, %d deletes, %d snapshot rows",
                 restorer.counts["U"], restorer.counts["D"], restorer.counts["S"])
    except Exception:
        conn.rollback()
        log.error("Restore failed; no changes were made.")
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
