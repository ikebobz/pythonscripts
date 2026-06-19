import os
import json
import argparse
from datetime import datetime, timezone
import pandas as pd
import psycopg2
from psycopg2.extras import RealDictCursor
import json
from pathlib import Path


def load_last_run(manifest_path, default_value=None):
    p = Path(manifest_path)
    if not p.exists():
        if default_value is None:
            raise ValueError("manifest.json not found and no default last_run provided")
        return datetime.fromisoformat(default_value.replace("Z", "+00:00"))

    with p.open("r", encoding="utf-8") as f:
        manifest = json.load(f)

    last_run = manifest.get("last_run")
    if not last_run:
        if default_value is None:
            raise ValueError("manifest.json does not contain last_run")
        return datetime.fromisoformat(default_value.replace("Z", "+00:00"))

    return datetime.fromisoformat(last_run.replace("Z", "+00:00"))
    
def save_manifest(manifest_path, manifest):
    manifest["generated_at"] = datetime.now(timezone.utc).isoformat()
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, default=str)
        


# ... perform backup ...




WATERMARK_COLUMNS = ["date_modified", "last_modified_date"]

def norm(s):
    return str(s).strip().lower()

def load_table_metadata(csv_path):
    df = pd.read_csv(csv_path)
    df.columns = [c.strip().lower() for c in df.columns]

    required = {"schema_name", "table_name", "column_name"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Missing columns in CSV: {sorted(missing)}")

    grouped = {}
    for (schema_name, table_name), g in df.groupby(["schema_name", "table_name"]):
        cols = [norm(x) for x in g["column_name"].tolist()]
        wm = next((c for c in WATERMARK_COLUMNS if c in cols), None)
        if wm:
            grouped[(schema_name, table_name)] = {
                "columns": g["column_name"].tolist(),
                "watermark_column": wm,
            }
    return grouped

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
        rows = cur.fetchall()
    return [r["pk_column"] for r in rows]

def fetch_changes(conn, schema_name, table_name, watermark_column, last_run):
    sql = f"""
    SELECT *
    FROM "{schema_name}"."{table_name}"
    WHERE "{watermark_column}" > %s
    ORDER BY "{watermark_column}" ASC
    """
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(sql, (last_run,))
        return cur.fetchall()

def ensure_dir(path):
    os.makedirs(path, exist_ok=True)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--last-run", required=True, help="ISO timestamp, e.g. 2026-06-18T00:00:00Z")
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", default=5432, type=int)
    parser.add_argument("--dbname", required=True)
    parser.add_argument("--user", required=True)
    parser.add_argument("--password", required=True)
    args = parser.parse_args()

    #last_run = datetime.fromisoformat(args.last_run.replace("Z", "+00:00"))
    manifest_path = os.path.join(args.output, "manifest.json")
    last_run = load_last_run(manifest_path, default_value=args.last_run)

    manifest = {
    "generated_at": datetime.now(timezone.utc).isoformat(),
    "last_run": last_run.isoformat(),
    "tables": []
               }

    conn = psycopg2.connect(
        host=args.host,
        port=args.port,
        dbname=args.dbname,
        user=args.user,
        password=args.password,
    )

    ensure_dir(args.output)
    tables = load_table_metadata(args.csv)

    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "last_run": last_run.isoformat(),
        "tables": [],
    }

    try:
        for (schema_name, table_name), meta in tables.items():
            pk_cols = get_primary_key(conn, schema_name, table_name)
            if not pk_cols:
                continue

            rows = fetch_changes(conn, schema_name, table_name, meta["watermark_column"], last_run)
            if not rows:
                continue

            table_dir = os.path.join(args.output, schema_name, table_name)
            ensure_dir(table_dir)

            out_file = os.path.join(table_dir, f"{datetime.now(timezone.utc).date().isoformat()}.jsonl")
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

            manifest["tables"].append({
                "schema_name": schema_name,
                "table_name": table_name,
                "watermark_column": meta["watermark_column"],
                "primary_key": pk_cols,
                "output_file": out_file,
                "row_count": len(rows),
            })

        with open(os.path.join(args.output, "manifest.json"), "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2, default=str)

    finally:
        conn.close()
    manifest["last_run"] = datetime.now(timezone.utc).isoformat()
    save_manifest(manifest_path, manifest)

if __name__ == "__main__":
    main()