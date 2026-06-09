import json
import psycopg2             # replace with sqlite3 if needed
from psycopg2 import extras
import os
from dotenv import load_dotenv
load_dotenv()

def get_conn():
    conn = psycopg2.connect(os.getenv("DATABASE_URL"))
    conn.cursor_factory = extras.RealDictCursor
    return conn

# initialze the DB

def init_db():
    conn = get_conn()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS companies (
            id SERIAL PRIMARY KEY,
            name TEXT,
            addresses TEXT,
            phones TEXT,
            websites TEXT,
            company_info TEXT,
            additional_info TEXT,
            source_folder TEXT,
            notes TEXT
        )
    """)

    # migrate existing DBs that don't have the notes column yet
    try:
        cursor.execute("ALTER TABLE companies ADD COLUMN notes TEXT")
        conn.commit()
    except psycopg2.errors.DuplicateColumn:
        conn.rollback() # column already exists

    # migrate existing DBs that don't have the taiga_project_id column yet
    try:
        cursor.execute("ALTER TABLE companies ADD COLUMN taiga_project_id INTEGER")
        conn.commit()
    except psycopg2.errors.DuplicateColumn:
        conn.rollback()

    try:
        cursor.execute("ALTER TABLE companies ADD COLUMN taiga_project_slug TEXT")
        conn.commit()
    except psycopg2.errors.DuplicateColumn:
        conn.rollback()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS people (
            id SERIAL PRIMARY KEY,
            company_id INTEGER,
            name TEXT,
            role TEXT,
            phones TEXT,
            emails TEXT,
            FOREIGN KEY (company_id) REFERENCES companies(id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS company_categories (
            id SERIAL PRIMARY KEY,
            company_id INTEGER NOT NULL,
            category TEXT NOT NULL,
            FOREIGN KEY (company_id) REFERENCES companies(id)
        )
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_company_categories_company
        ON company_categories(company_id)
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_company_categories_category
        ON company_categories(category)
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS flagged (
            id SERIAL PRIMARY KEY,
            company_id INTEGER NOT NULL,
            reason TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            created_at TEXT NOT NULL,
            resolved_at TEXT,
            FOREIGN KEY (company_id) REFERENCES companies(id)
        )
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_flagged_company
        ON flagged(company_id)
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_flagged_status
        ON flagged(status)
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS tasks (
        id SERIAL PRIMARY KEY,
        company_id INTEGER,
        title TEXT,
        description TEXT,
        assignee TEXT,
        due_date TEXT,
        status TEXT DEFAULT 'To Do',
        source_transcript TEXT,
        created_at TEXT,
        FOREIGN KEY (company_id) REFERENCES companies(id)
        )
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_tasks_company
        ON tasks(company_id)
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_tasks_status
        ON tasks(status)
    """)

    conn.commit()
    conn.close()

# There was something wrong with json.dump so this fixed it. Helps handle Nulls

def normalize(val):
    if val is None:
        return None
    if isinstance(val, list):
        return json.dumps(val)
    return json.dumps([val])

# Merges existing companies if they are the same. 

def _merge_json_lists(existing_json, new_vals) -> str:
    existing = []
    if existing_json:
        try:
            parsed = json.loads(existing_json)
            if isinstance(parsed, list):
                existing = parsed
        except (json.JSONDecodeError, TypeError):
            pass

    if not new_vals:
        return json.dumps(existing)

    if isinstance(new_vals, str):
        try:
            new_vals = json.loads(new_vals)
        except (json.JSONDecodeError, TypeError):
            new_vals = [new_vals]

    if not isinstance(new_vals, list):
        new_vals = [new_vals]

    seen = {str(v).strip().lower() for v in existing}
    for v in new_vals:
        if v and str(v).strip().lower() not in seen:
            existing.append(v)
            seen.add(str(v).strip().lower())

    return json.dumps(existing)


def _merge_text(existing, new):
    """Keep existing value if set; fill in from new only if missing."""
    if existing:
        return existing
    return new or existing


# Saves company to DB (Updated to handle merging)

def save_company(data: dict, source_folder: str) -> int | None:
    company = data.get("company", {})
    name = (company.get("name") or "").strip()

    conn = get_conn()
    cursor = conn.cursor()

    cursor.execute(
        "SELECT * FROM companies WHERE TRIM(LOWER(name)) = %s",
        (name.lower(),)
    )
    existing = cursor.fetchone()

    if existing:
        company_id = existing["id"]

        merged_addresses    = _merge_json_lists(existing["addresses"],   company.get("addresses"))
        merged_phones       = _merge_json_lists(existing["phones"],      company.get("phones"))
        merged_websites     = _merge_json_lists(existing["websites"],    company.get("websites"))
        merged_company_info = _merge_text(existing["company_info"],      company.get("company_info"))
        merged_additional   = _merge_text(existing["additional_info"],   company.get("additional_info"))
        merged_emails = _merge_json_lists(existing["emails"], company.get("emails"))

        cursor.execute("""
            UPDATE companies
            SET addresses       = %s,
                phones          = %s,
                emails          = %s,
                websites        = %s,
                company_info    = %s,
                additional_info = %s
            WHERE id = %s
        """, (
            merged_addresses,
            merged_phones,
            merged_emails,
            merged_websites,
            merged_company_info,
            merged_additional,
            company_id,
        ))
        print(f"  Merged into existing company '{name}' (id={company_id})")

    else:
        cursor.execute("""
            INSERT INTO companies (name, addresses, phones, emails, websites, company_info, additional_info, source_folder)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (
            name or company.get("name"),
            normalize(company.get("addresses")),
            normalize(company.get("phones")),
            normalize(company.get("emails")),
            normalize(company.get("websites")),
            company.get("company_info"),
            company.get("additional_info"),
            source_folder,
        ))
        company_id = cursor.fetchone()["id"]
        print(f"  Inserted new company '{name}' (id={company_id})")

    conn.commit()
    conn.close()
    return company_id


# Saves people to DB (or updates existing)

def save_people(company_id: int | None, data: dict):
    conn = get_conn()
    # conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    ids =[]

    for person in data.get("people", []):
        name = (person.get("name") or "").strip()
        if not name:
            continue

        cursor.execute(
            "SELECT * FROM people WHERE company_id = %s AND TRIM(LOWER(name)) = %s",
            (company_id, name.lower())
        )
        existing = cursor.fetchone()

        if existing:
            merged_phones = _merge_json_lists(existing["phones"], person.get("phones"))
            merged_emails = _merge_json_lists(existing["emails"], person.get("emails"))
            merged_role   = _merge_text(existing["role"], person.get("role"))

            cursor.execute("""
                UPDATE people
                SET phones = %s,
                    emails = %s,
                    role   = %s
                WHERE id = %s
            """, (merged_phones, merged_emails, merged_role, existing["id"]))

        else:
            cursor.execute("""
                INSERT INTO people (company_id, name, role, phones, emails)
                VALUES (%s, %s, %s, %s, %s)
            """, (
                company_id,
                name,
                person.get("role"),
                normalize(person.get("phones")),
                normalize(person.get("emails")),
            ))

        if existing:
            ids.append(existing["id"])
        else:
            cursor.execute("SELECT id FROM people WHERE company_id = %s AND TRIM(LOWER(name)) = %s",
                          (company_id, name.lower()))
            row = cursor.fetchone()
            if row:
                ids.append(row["id"])


    conn.commit()
    conn.close()
    return ids


# Assign categories

def save_categories(company_id: int, categories: list[str]):
    if not categories:
        return

    conn = get_conn()
    cursor = conn.cursor()

    cursor.execute(
        "DELETE FROM company_categories WHERE company_id = %s",
        (company_id,)
    )
    cursor.executemany(
        "INSERT INTO company_categories (company_id, category) VALUES (%s, %s)",
        [(company_id, cat) for cat in categories]
    )

    conn.commit()
    conn.close()


# Shows categories asscocitaed with a company

def get_categories(company_id: int) -> list[str]:
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT category FROM company_categories WHERE company_id = %s ORDER BY category",
        (company_id,)
    )
    rows = cursor.fetchall()
    conn.close()
    return [row[0] for row in rows]


# Shows all existing categories

def get_all_categories() -> list[str]:
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT DISTINCT category FROM company_categories ORDER BY category"
    )
    rows = cursor.fetchall()
    conn.close()
    return [row[0] for row in rows]

def save_flag(company_id: int, reason: str):
    """
    Insert a flag for a company. Skips if an active flag with the same
    reason already exists — safe to call on every pipeline run.
    """
    from datetime import datetime
    conn = get_conn()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id FROM flagged
        WHERE company_id = %s AND reason = %s AND status = 'active'
    """, (company_id, reason))

    if cursor.fetchone() is None:
        cursor.execute("""
            INSERT INTO flagged (company_id, reason, status, created_at)
            VALUES (%s, %s, 'active', %s)
        """, (company_id, reason, datetime.now().isoformat()))

    conn.commit()
    conn.close()


def resolve_flag(company_id: int, reason: str, status: str):
    """
    Update all active flags for a company+reason to the given status.
    status should be 'resolved' or 'dismissed'.
    """
    from datetime import datetime
    conn = get_conn()
    cursor = conn.cursor()

    cursor.execute("""
        UPDATE flagged
        SET status = %s, resolved_at = %s
        WHERE company_id = %s AND reason = %s AND status = 'active'
    """, (status, datetime.now().isoformat(), company_id, reason))
    conn.commit()
    conn.close()


def get_flagged() -> list:
    """
    Return all active flagged companies joined with company name
    and their raw products from enrichment.
    """
    conn = get_conn()
    # conn.row_factory = psycopg2.extras.RealDictCursor
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            f.id AS flag_id,
            f.company_id,
            f.reason,
            f.created_at,
            c.name AS company_name,
            e.products,
            e.domain
        FROM flagged f
        JOIN companies c ON c.id = f.company_id
        LEFT JOIN enrichment e ON e.contact_id = f.company_id
        WHERE f.status = 'active'
        ORDER BY f.created_at DESC
    """)

    rows = cursor.fetchall()
    conn.close()
    return rows


# if __name__ == "__main__":
#     init_db("contacts.db")
