# Zoho cleanup and export script

import argparse
import csv
import json
import sqlite3
from pathlib import Path


DB_PATH = str(Path(__file__).parent / "contacts.db")
OUT_PATH = str(Path(__file__).parent / "zoho_accounts.csv")


def parse_json_list(val) -> list:
    if not val:
        return []
    try:
        parsed = json.loads(val)
        if isinstance(parsed, list):
            return [str(v).strip() for v in parsed if v]
        return [str(parsed).strip()]
    except (json.JSONDecodeError, TypeError):
        return [str(val).strip()]


def export(db_path: str, out_path: str):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, name, phones, websites, addresses, company_info, additional_info
        FROM companies
        ORDER BY id
    """)
    rows = cursor.fetchall()
    conn.close()

    print(f"Exporting {len(rows)} companies to {out_path}...")

    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "Account Name",
            "Phone",
            "Website",
            "Billing Street",
            "Description",
        ])
        writer.writeheader()

        for row in rows:
            phones   = parse_json_list(row["phones"])
            websites = parse_json_list(row["websites"])
            addresses = parse_json_list(row["addresses"])

            # combine company_info and additional_info into description
            desc_parts = [row["company_info"], row["additional_info"]]
            description = " | ".join(p for p in desc_parts if p)

            writer.writerow({
                "Account Name":  row["name"] or "",
                "Phone":         phones[0] if phones else "",
                "Website":       websites[0] if websites else "",
                "Billing Street": addresses[0] if addresses else "",
                "Description":   description,
            })

    print(f"Done. File saved to: {out_path}")
    print("Next steps:")
    print("  1. Go to Zoho CRM -> Accounts -> Import")
    print("  2. Upload this CSV file")
    print("  3. Map columns when prompted (they should auto-match)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Export companies to Zoho-compatible CSV")
    parser.add_argument("--db", default=DB_PATH, help="Path to SQLite database")
    parser.add_argument("--out", default=OUT_PATH, help="Output CSV file path")
    args = parser.parse_args()
    export(db_path=args.db, out_path=args.out)