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
  P  patch: update only the given columns of an existing row (used in
     "parked" files, see below)

Everything is applied in ONE transaction: if anything fails, nothing changes.

Unique-key conflicts: the target database may already hold a row with the same
unique value (a uuid, a module name, a code...) under a DIFFERENT primary key,
e.g. a fresh LAMISPlus install that created its own base_module rows.
  --on-conflict replace  (default) delete the target's conflicting row and load
                         the backup row, so the backup wins
  --on-conflict skip     keep the target's row and skip the backup row
  --on-conflict fail     stop with an error (nothing is changed)
Every conflict is logged and written to logs/restore_conflicts_<time>.csv.
Use --dry-run to see what would happen without changing anything.

Pre-restore check: before anything is changed, every backup record is compared
with the target database, and tables or columns that exist in the backup but
not in the target are listed (usually a LAMISPlus module that is not installed,
or an older version of it, on the target). --check-only stops after the list.
  --on-missing fail  (default) stop before changing anything
  --on-missing skip  restore everything else; the records of missing tables and
                     the values of missing columns are saved ("parked") in
                     parked/parked_<time>.zip. Install/upgrade the module on
                     the target, then restore that zip like any backup:
                       restore_script.py --input parked/parked_<time>.zip ...
By default foreign-key checks and triggers are switched off for the restore
session (session_replication_role = replica) so tables can be loaded in any
order; this needs a superuser such as "postgres". Use --keep-triggers to disable
that behaviour.
"""
import argparse
import base64
import csv
import getpass
import io
import json
import logging
import os
import shutil
import sys
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

import psycopg2
import psycopg2.errors
from psycopg2.extras import Json

log = logging.getLogger("restore")


def qi(name):
    return '"' + str(name).replace('"', '""') + '"'


class Restorer:
    def __init__(self, conn, on_conflict="replace"):
        self.conn = conn
        self.on_conflict = on_conflict
        self.unique = {}            # (schema, table) -> {index name: {cols, expr, pred}}
        self.conflicts = []         # one dict per conflicting row handled
        self.columns = {}           # (schema, table) -> {column: data_type}
        self.always_identity = {}   # (schema, table) -> {columns GENERATED ALWAYS}
        self.touched = set()        # tables written to, for sequence resync
        self.warned = set()
        self.counts = {"U": 0, "D": 0, "S": 0, "P": 0}
        self.parker = None             # set when --on-missing skip
        self.missing_tables = set()
        self.missing_columns = {}      # (schema, table) -> set of columns

    def table_columns(self, schema, table):
        key = (schema, table)
        if key not in self.columns:
            with self.conn.cursor() as cur:
                cur.execute("SELECT column_name, data_type, is_identity, identity_generation "
                            "FROM information_schema.columns "
                            "WHERE table_schema = %s AND table_name = %s", (schema, table))
                rows = cur.fetchall()
            self.columns[key] = {r[0]: r[1] for r in rows}
            self.always_identity[key] = {r[0] for r in rows
                                         if r[2] == "YES" and r[3] == "ALWAYS"}
        return self.columns[key]

    def unique_indexes(self, schema, table):
        """Unique indexes other than the primary key."""
        key = (schema, table)
        if key not in self.unique:
            sql = """
            SELECT i.relname,
                   (SELECT string_agg(a.attname, ',' ORDER BY k.ord)
                      FROM unnest(x.indkey) WITH ORDINALITY AS k(attnum, ord)
                      JOIN pg_attribute a ON a.attrelid = x.indrelid AND a.attnum = k.attnum),
                   0 = ANY (x.indkey::int2[]),
                   pg_get_expr(x.indpred, x.indrelid)
            FROM pg_index x
            JOIN pg_class i ON i.oid = x.indexrelid
            JOIN pg_class c ON c.oid = x.indrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = %s AND c.relname = %s
              AND x.indisunique AND NOT x.indisprimary
            """
            with self.conn.cursor() as cur:
                cur.execute(sql, (schema, table))
                self.unique[key] = {
                    name: {"cols": (cols or "").split(","), "expr": bool(expr), "pred": pred}
                    for name, cols, expr, pred in cur.fetchall()
                }
        return self.unique[key]

    def find_conflicts(self, schema, table, idx, row, pk_cols):
        """Target rows holding the same unique value as the backup row (other pk)."""
        cols = idx["cols"]
        if idx["expr"] or any(c not in row or row[c] is None for c in cols):
            return []
        where = [f"{qi(c)} = %s" for c in cols]
        vals = [row[c] for c in cols]
        if idx["pred"]:
            where.append(f"({idx['pred']})")
        if pk_cols and all(c in row for c in pk_cols):
            where.append("NOT (" + " AND ".join(f"{qi(c)} = %s" for c in pk_cols) + ")")
            vals += [row[c] for c in pk_cols]
        shown = ", ".join(qi(c) for c in pk_cols) if pk_cols else "NULL"
        with self.conn.cursor() as cur:
            cur.execute(f"SELECT ctid::text, {shown} FROM {qi(schema)}.{qi(table)} "
                        f"WHERE {' AND '.join(where)}", vals)
            return [(r[0], dict(zip(pk_cols, r[1:])) if pk_cols else None)
                    for r in cur.fetchall()]

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
                    log.warning("Column %s.%s.%s not in target database; %s", schema, table, c,
                                "its values are parked" if self.parker else "ignored")
                continue
            out[c] = self.adapt(cols[c], v)
        return out

    def upsert(self, schema, table, pk_cols, row):
        row = self.prepared_row(schema, table, row)
        cols = list(row.keys())
        qt = f"{qi(schema)}.{qi(table)}"
        insert_cols = ", ".join(qi(c) for c in cols)
        placeholders = ", ".join(["%s"] * len(cols))
        # Identity columns defined GENERATED ALWAYS reject explicit values unless
        # told to keep them; a restore must keep the original ids.
        always = self.always_identity.get((schema, table), set())
        overriding = " OVERRIDING SYSTEM VALUE" if always & set(cols) else ""
        self.touched.add((schema, table))
        if pk_cols:
            target = ", ".join(qi(c) for c in pk_cols)
            update_cols = [c for c in cols if c not in pk_cols and c not in always]
            if update_cols:
                sets = ", ".join(f"{qi(c)} = EXCLUDED.{qi(c)}" for c in update_cols)
                tail = f"ON CONFLICT ({target}) DO UPDATE SET {sets}"
            else:
                tail = f"ON CONFLICT ({target}) DO NOTHING"
        else:
            tail = ""
        sql = f"INSERT INTO {qt} ({insert_cols}){overriding} VALUES ({placeholders}) {tail}"
        values = list(row.values())

        uniq = self.unique_indexes(schema, table)
        if not uniq or self.on_conflict == "fail":
            with self.conn.cursor() as cur:
                cur.execute(sql, values)
            return

        # Insert inside a savepoint so a unique-key clash can be undone and handled.
        for _ in range(len(uniq) + 1):
            with self.conn.cursor() as cur:
                try:
                    cur.execute(f"SAVEPOINT restore_row; {sql}; RELEASE SAVEPOINT restore_row",
                                values)
                    return
                except psycopg2.errors.UniqueViolation as e:
                    cur.execute("ROLLBACK TO SAVEPOINT restore_row; RELEASE SAVEPOINT restore_row")
                    name = getattr(e.diag, "constraint_name", None)
                    idx = uniq.get(name)
                    found = self.find_conflicts(schema, table, idx, row, pk_cols) if idx else []
                    if not found:
                        raise
                    backup_pk = {c: row.get(c) for c in pk_cols}
                    for ctid, target_pk in found:
                        self.conflicts.append({
                            "table": f"{schema}.{table}", "constraint": name,
                            "columns": ",".join(idx["cols"]),
                            "value": ",".join(str(row[c]) for c in idx["cols"]),
                            "backup_pk": json.dumps(backup_pk, default=str),
                            "target_pk": json.dumps(target_pk, default=str),
                            "action": "replaced" if self.on_conflict == "replace" else "skipped",
                        })
                    if self.on_conflict == "skip":
                        return
                    for ctid, _ in found:
                        cur.execute(f"DELETE FROM {qt} WHERE ctid = %s::tid", (ctid,))
        raise RuntimeError(f"{schema}.{table}: could not resolve unique-key conflicts for {row}")

    def patch(self, schema, table, pk, values):
        """UPDATE only the given columns of an existing row."""
        cols = self.table_columns(schema, table)
        sets = [c for c in values if c in cols and c not in pk]
        if not sets:
            return
        always = self.always_identity.get((schema, table), set())
        sets = [c for c in sets if c not in always]
        sql = (f"UPDATE {qi(schema)}.{qi(table)} SET "
               + ", ".join(f"{qi(c)} = %s" for c in sets)
               + " WHERE " + " AND ".join(f"{qi(c)} = %s" for c in pk))
        params = ([self.adapt(cols[c], values[c]) for c in sets]
                  + [self.adapt(cols.get(c), v) for c, v in pk.items()])
        with self.conn.cursor() as cur:
            cur.execute(sql, params)

    def delete(self, schema, table, pk):
        cols = self.table_columns(schema, table)
        where = " AND ".join(f"{qi(c)} = %s" for c in pk)
        values = [self.adapt(cols.get(c), v) for c, v in pk.items()]
        with self.conn.cursor() as cur:
            cur.execute(f"DELETE FROM {qi(schema)}.{qi(table)} WHERE {where}", values)

    def resync_sequences(self):
        """Move identity/serial sequences past the highest restored id, so the
        application's next insert does not collide with a restored row."""
        n = 0
        for schema, table in sorted(self.touched):
            qt = f"{qi(schema)}.{qi(table)}"
            for col in self.columns.get((schema, table), {}):
                with self.conn.cursor() as cur:
                    cur.execute("SELECT pg_get_serial_sequence(%s, %s)", (qt, col))
                    seq = cur.fetchone()[0]
                    if not seq:
                        continue
                    cur.execute(f"SELECT setval(%s, coalesce(max({qi(col)}), 1), "
                                f"max({qi(col)}) IS NOT NULL) FROM {qt}", (seq,))
                    n += 1
        if n:
            log.info("Reset %d id sequence(s) to follow the restored data", n)

    def report_conflicts(self, log_dir):
        if not self.conflicts:
            return None
        groups = {}
        for c in self.conflicts:
            groups.setdefault((c["table"], c["constraint"], c["columns"], c["action"]), []).append(c)
        log.warning("%d unique-key conflict(s) with rows already in the target database:",
                    len(self.conflicts))
        for (tbl, cons, cols, action), items in sorted(groups.items()):
            ex = items[0]
            log.warning("  %-40s %-35s %5d %s  e.g. %s=%s (target %s, backup %s)", tbl, cons,
                        len(items), action, cols, ex["value"], ex["target_pk"], ex["backup_pk"])
        log_dir.mkdir(parents=True, exist_ok=True)
        stamp, n = f"{datetime.now():%Y%m%d_%H%M%S}", 1
        path = log_dir / f"restore_conflicts_{stamp}.csv"
        while path.exists():
            n += 1
            path = log_dir / f"restore_conflicts_{stamp}_{n}.csv"
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(self.conflicts[0].keys()))
            w.writeheader()
            w.writerows(self.conflicts)
        log.warning("Full list: %s", path)
        return path

    def apply_lines(self, lines, label):
        cleared = set()   # tables already emptied for a snapshot in this file
        n = 0
        source = label.rsplit("/", 2)[0]     # the backup run this file belongs to
        for line in lines:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            schema, table, op = rec["schema_name"], rec["table_name"], rec.get("op", "U")
            key = (schema, table)
            if key in self.missing_tables:          # park the whole record
                self.parker.write(schema, table, line, "records", source)
                n += 1
                continue
            gone = self.missing_columns.get(key)
            if gone and op == "S":
                # no primary key to patch by: park every snapshot record of this table
                # (including "table is now empty" markers); restoring them later
                # reloads the whole table with all columns
                self.parker.write(schema, table, line, "records", source)
            elif gone and rec.get("row_data"):
                present = gone & rec["row_data"].keys()
                if present and op == "U" and rec.get("pk"):
                    # keep the values of the missing columns as a patch
                    patch = {"schema_name": schema, "table_name": table, "op": "P",
                             "pk": rec["pk"],
                             "row_data": {c: rec["row_data"][c] for c in present}}
                    self.parker.write(schema, table, json.dumps(patch), "patches", source)
            if op == "U":
                self.upsert(schema, table, list((rec.get("pk") or {}).keys()), rec["row_data"])
            elif op == "D":
                self.delete(schema, table, rec["pk"])
            elif op == "P":
                self.patch(schema, table, rec["pk"], rec["row_data"])
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


class Parker:
    """Collects records that cannot be applied now into a zip that restore_script.py
    can apply later (same layout as a backup archive)."""

    def __init__(self, out_dir, dry_run=False):
        self.out_dir = Path(out_dir)
        self.dry_run = dry_run
        self.name = f"parked_{datetime.now():%Y%m%d_%H%M%S}"
        self.tmp = Path(tempfile.mkdtemp(prefix="restore_parked_"))
        self.files = {}
        self.counts = {}     # (schema, table) -> {"records": n, "patches": n}
        self.batches = {}    # source archive -> folder number, to keep runs apart

    def write(self, schema, table, line, kind, source):
        c = self.counts.setdefault((schema, table), {"records": 0, "patches": 0})
        c[kind] += 1
        if self.dry_run:
            return
        # One sub-folder per source backup, numbered in restore order, so snapshot
        # records from different runs stay separate and are re-applied in order.
        batch = self.batches.setdefault(source, len(self.batches) + 1)
        f = self.files.get((batch, schema, table))
        if f is None:
            path = self.tmp / self.name / f"{batch:04d}" / schema / f"{table}.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)
            f = self.files[(batch, schema, table)] = open(path, "w", encoding="utf-8")
        f.write(line if line.endswith("\n") else line + "\n")

    def finish(self, info):
        """Write the zip. Called only after the restore committed."""
        for f in self.files.values():
            f.close()
        if self.dry_run or not self.counts:
            self.discard()
            return None
        run_dir = self.tmp / self.name
        run_dir.mkdir(parents=True, exist_ok=True)
        info["tables"] = [{"schema_name": s, "table_name": t, **c}
                          for (s, t), c in sorted(self.counts.items())]
        with open(run_dir / "parked_info.json", "w", encoding="utf-8") as f:
            json.dump(info, f, indent=2)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        zip_base = self.out_dir / self.name
        n = 1
        while Path(str(zip_base) + ".zip").exists():
            n += 1
            zip_base = self.out_dir / f"{self.name}_{n}"
        path = shutil.make_archive(str(zip_base), "zip", root_dir=self.tmp, base_dir=self.name)
        self.discard()
        return path

    def discard(self):
        for f in self.files.values():
            f.close()
        shutil.rmtree(self.tmp, ignore_errors=True)


def preflight(restorer, input_path):
    """Read every record once and compare tables/columns with the target.
    Returns (missing_tables, missing_columns, table_count)."""
    stats = {}
    for label, lines in sources(input_path, quiet=True):
        for line in lines:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            key = (rec["schema_name"], rec["table_name"])
            st = stats.setdefault(key, {"records": 0, "cols": {}})
            st["records"] += 1
            for col, val in (rec.get("row_data") or {}).items():
                st["cols"][col] = st["cols"].get(col, 0) + (val is not None)
    missing_tables, missing_columns = {}, {}
    for key, st in sorted(stats.items()):
        target_cols = restorer.table_columns(*key)
        if not target_cols:
            missing_tables[key] = st["records"]
            continue
        gone = {c: n for c, n in st["cols"].items() if c not in target_cols}
        if gone:
            missing_columns[key] = gone
    return missing_tables, missing_columns, len(stats)


def report_missing(missing_tables, missing_columns, n_tables):
    if not missing_tables and not missing_columns:
        log.info("Pre-restore check: all %d tables and their columns exist in the target.",
                 n_tables)
        return
    log.warning("Pre-restore check: %d of %d tables in the backup are missing in the target, "
                "%d table(s) have missing columns.",
                len(missing_tables), n_tables, len(missing_columns))
    for (s, t), n in missing_tables.items():
        log.warning("  MISSING TABLE   %-55s %9s records", f"{s}.{t}", f"{n:,}")
    for (s, t), cols in missing_columns.items():
        for c, n in sorted(cols.items()):
            log.warning("  MISSING COLUMN  %-55s %9s rows with a value", f"{s}.{t}.{c}", f"{n:,}")


def sources(input_path, quiet=False):
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
            if not quiet:
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
    parser.add_argument("--on-conflict", choices=["replace", "skip", "fail"], default="replace",
                        help="what to do when the target already has a row with the same "
                             "unique value under another id (default: replace)")
    parser.add_argument("--dry-run", action="store_true",
                        help="do the whole restore, report it, then undo it")
    parser.add_argument("--on-missing", choices=["fail", "skip"], default="fail",
                        help="tables/columns in the backup but not in the target: stop "
                             "(default) or restore the rest and park them for later")
    parser.add_argument("--check-only", action="store_true",
                        help="only run the pre-restore check and exit")
    parser.add_argument("--parked-dir", default=None,
                        help="where parked zips go (default: parked\\ next to this script)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                        format="%(asctime)s %(levelname)s %(message)s")
    password = args.password or getpass.getpass(f"Database password for {args.user}: ")

    conn = psycopg2.connect(host=args.host, port=args.port, dbname=args.dbname,
                            user=args.user, password=password)
    conn.autocommit = False
    restorer = Restorer(conn, on_conflict=args.on_conflict)
    log_dir = Path(__file__).resolve().with_name("logs")
    try:
        missing_tables, missing_columns, n_tables = preflight(restorer, args.input)
        report_missing(missing_tables, missing_columns, n_tables)
        anything_missing = bool(missing_tables or missing_columns)
        if args.check_only:
            conn.rollback()
            return 1 if anything_missing else 0
        if anything_missing and args.on_missing == "fail":
            conn.rollback()
            log.error("Nothing was changed. Install or upgrade the LAMISPlus module(s) that own "
                      "these tables on the target, or rerun with --on-missing skip to restore "
                      "everything else and park the rest for later.")
            return 3
        if anything_missing:
            restorer.parker = Parker(args.parked_dir or
                                     Path(__file__).resolve().with_name("parked"),
                                     dry_run=args.dry_run)
            restorer.missing_tables = set(missing_tables)
            restorer.missing_columns = {k: set(v) for k, v in missing_columns.items()}

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
        restorer.resync_sequences()
        restorer.report_conflicts(log_dir)
        summary = (f"{restorer.counts['U']} upserts, {restorer.counts['D']} deletes, "
                   f"{restorer.counts['S']} snapshot rows, {restorer.counts['P']} column patches")
        if args.dry_run:
            conn.rollback()
            log.info("DRY RUN - nothing was changed. Would have applied: %s", summary)
        else:
            conn.commit()
            log.info("Restore committed: %s", summary)
        if restorer.parker:
            parked = sum(c["records"] for c in restorer.parker.counts.values())
            patches = sum(c["patches"] for c in restorer.parker.counts.values())
            path = restorer.parker.finish({
                "created_at": datetime.now().isoformat(), "source": str(args.input),
                "target_db": args.dbname,
                "missing_tables": [f"{s}.{t}" for s, t in missing_tables],
                "missing_columns": {f"{s}.{t}": sorted(c) for (s, t), c in missing_columns.items()},
            })
            if args.dry_run:
                log.info("Would park %d whole records and %d missing-column patches.",
                         parked, patches)
            else:
                log.warning("Parked %d whole records and %d missing-column patches in %s",
                            parked, patches, path)
                log.warning("After installing/upgrading the module(s) on the target, restore "
                            "that file:  restore_script.py --input \"%s\" ...", path)
        return 0
    except Exception:
        if restorer.parker:
            restorer.parker.discard()
        conn.rollback()
        log.error("Restore failed; no changes were made.")
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
