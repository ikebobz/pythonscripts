import json
import argparse
from pathlib import Path
import psycopg2

def quote_ident(name):
    return '"' + name.replace('"', '""') + '"'

def upsert_row(conn, schema_name, table_name, pk_cols, row_data):
    cols = list(row_data.keys())
    insert_cols = ", ".join(quote_ident(c) for c in cols)
    placeholders = ", ".join(["%s"] * len(cols))
    conflict_target = ", ".join(quote_ident(c) for c in pk_cols)
    update_cols = [c for c in cols if c not in pk_cols]

    if update_cols:
        set_clause = ", ".join(f"{quote_ident(c)} = EXCLUDED.{quote_ident(c)}" for c in update_cols)
        sql = f"""
        INSERT INTO {quote_ident(schema_name)}.{quote_ident(table_name)} ({insert_cols})
        VALUES ({placeholders})
        ON CONFLICT ({conflict_target})
        DO UPDATE SET {set_clause}
        """
    else:
        sql = f"""
        INSERT INTO {quote_ident(schema_name)}.{quote_ident(table_name)} ({insert_cols})
        VALUES ({placeholders})
        ON CONFLICT ({conflict_target})
        DO NOTHING
        """
    with conn.cursor() as cur:
        cur.execute(sql, list(row_data.values()))

def apply_jsonl(conn, file_path):
    with open(file_path, "r", encoding="utf-8") as f:
        for line in f:
            rec = json.loads(line)
            upsert_row(conn, rec["schema_name"], rec["table_name"], list(rec["pk"].keys()), rec["row_data"])

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", required=True)
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", default=5432, type=int)
    parser.add_argument("--dbname", required=True)
    parser.add_argument("--user", required=True)
    parser.add_argument("--password", required=True)
    args = parser.parse_args()

    conn = psycopg2.connect(
        host=args.host,
        port=args.port,
        dbname=args.dbname,
        user=args.user,
        password=args.password,
    )
    conn.autocommit = False

    try:
        for file_path in sorted(Path(args.input_dir).rglob("*.jsonl")):
            apply_jsonl(conn, str(file_path))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

if __name__ == "__main__":
    main()