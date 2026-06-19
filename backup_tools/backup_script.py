import os
import json
import argparse
import shutil
from pathlib import Path
from datetime import datetime, timezone
import pandas as pd
import psycopg2
from psycopg2.extras import RealDictCursor

WATERMARK_COLUMNS = ["date_modified", "last_modified_date"]

def norm(v):
    return str(v).strip().lower()

def iso_now():
    return datetime.now(timezone.utc).isoformat()

def parse_iso(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))

def load_csv_metadata(csv_path):
    df = pd.read_csv(csv_path)
    df.columns = [c.strip().lower() for c in df.columns]

    required = {"schema_name", "table_name", "column_name"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Missing columns in CSV: {sorted(missing)}")

    tables = {}
    for (schema_name, table_name), g in df.groupby(["schema_name", "table_name"]):
        colset = {norm(x) for x in g["column_name"].tolist()}
        wm = next((c for c in WATERMARK_COLUMNS if c in colset), None)
        if wm:
            tables[(schema_name, table_name)] = {"watermark_column": wm}
    return tables

def load_manifest(manifest_path, fallback_last_run=None):
    p = Path(manifest_path)
    if not p.exists():
        if not fallback_last_run:
            raise ValueError("manifest.json not found and --last-run not provided")
        return {
            "generated_at": iso_now(),
            "last_run": fallback_last_run,
            "archives": []
        }
    with p.open("r", encoding="utf-8") as f:
        return json.load(f)

def save_manifest(manifest_path, manifest):
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, default=str)

def get_primary_key(conn, schema_name, table_name):
    sql = """
    SELECT a.attname AS pk_column
    FROM pg_index i
    JOIN pg_attribute a
      ON a.attrelid = i.indrelid
     AND a.attnum = ANY(i.indkey)
    JOIN pg_class c
      ON c.oid = i.indrelid
    JOIN pg_namespace n
      ON n.oid = c.relnamespace
    WHERE i.indisprimary
      AND n.nspname = %s
      AND c.relname = %s
    ORDER BY array_position(i.indkey, a.attnum)
    """
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(sql, (schema_name, table_name))
        return [r["pk_column"] for r in cur.fetchall()]

def fetch_changes(conn, schema_name, table_name, watermark_column, last_run_dt):
    sql = f"""
    SELECT *
    FROM "{schema_name}"."{table_name}"
    WHERE "{watermark_column}" > %s
    ORDER BY "{watermark_column}" ASC
    """
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(sql, (last_run_dt,))
        return cur.fetchall()

def ensure_dir(path):
    os.makedirs(path, exist_ok=True)

def zip_folder(folder_path):
    folder_path = Path(folder_path)
    zip_base = str(folder_path)
    return shutil.make_archive(zip_base, "zip", root_dir=folder_path.parent, base_dir=folder_path.name)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--last-run", default=None)
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", default=5432, type=int)
    parser.add_argument("--dbname", required=True)
    parser.add_argument("--user", required=True)
    parser.add_argument("--password", required=True)
    args = parser.parse_args()

    output_dir = Path(args.output)
    ensure_dir(output_dir)

    manifest_path = output_dir / "manifest.json"
    manifest = load_manifest(manifest_path, fallback_last_run=args.last_run)
    last_run_dt = parse_iso(manifest["last_run"])

    run_date = datetime.now(timezone.utc).date().isoformat()
    run_dir = output_dir / run_date
    ensure_dir(run_dir)

    tables = load_csv_metadata(args.csv)

    conn = psycopg2.connect(
        host=args.host,
        port=args.port,
        dbname=args.dbname,
        user=args.user,
        password=args.password,
    )

    archive_entries = []
    try:
        for (schema_name, table_name), meta in tables.items():
            pk_cols = get_primary_key(conn, schema_name, table_name)
            if not pk_cols:
                continue

            rows = fetch_changes(conn, schema_name, table_name, meta["watermark_column"], last_run_dt)
            if not rows:
                continue

            schema_dir = run_dir / schema_name
            ensure_dir(schema_dir)

            out_file = schema_dir / f"{table_name}.jsonl"
            with open(out_file, "w", encoding="utf-8") as f:
                for row in rows:
                    pk = {k: row[k] for k in pk_cols}
                    modified_at = row.get(meta["watermark_column"])
                    payload = {
                        "schema_name": schema_name,
                        "table_name": table_name,
                        "op": "U",
                        "pk": pk,
                        "modified_at": modified_at.isoformat() if hasattr(modified_at, "isoformat") else str(modified_at),
                        "row_data": row,
                    }
                    f.write(json.dumps(payload, default=str) + "\n")

            archive_entries.append({
                "schema_name": schema_name,
                "table_name": table_name,
                "watermark_column": meta["watermark_column"],
                "primary_key": pk_cols,
                "output_file": str(out_file),
                "row_count": len(rows),
            })

        zip_path = zip_folder(run_dir)

        shutil.rmtree(run_dir)

        manifest["generated_at"] = iso_now()
        manifest["last_run"] = iso_now()
        manifest.setdefault("archives", []).append({
            "run_date": run_date,
            "zip_file": str(zip_path),
            "tables": archive_entries
        })
        save_manifest(manifest_path, manifest)

    finally:
        conn.close()

if __name__ == "__main__":
    main()