import sqlite3
import json


# initialze the DB

def init_db(db_path: str):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS companies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
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
    except sqlite3.OperationalError:
        pass  # column already exists

    # migrate existing DBs that don't have the taiga_project_id column yet
    try:
        cursor.execute("ALTER TABLE companies ADD COLUMN taiga_project_id INTEGER")
    except sqlite3.OperationalError:
        pass

    try:
        cursor.execute("ALTER TABLE companies ADD COLUMN taiga_project_slug TEXT")
    except sqlite3.OperationalError:
        pass

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS people (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
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
            id INTEGER PRIMARY KEY AUTOINCREMENT,
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
            id INTEGER PRIMARY KEY AUTOINCREMENT,
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
        id INTEGER PRIMARY KEY AUTOINCREMENT,
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

def save_company(db_path: str, data: dict, source_folder: str) -> int | None:
    company = data.get("company", {})
    name = (company.get("name") or "").strip()

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute(
        "SELECT * FROM companies WHERE TRIM(LOWER(name)) = ?",
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

        cursor.execute("""
            UPDATE companies
            SET addresses       = ?,
                phones          = ?,
                websites        = ?,
                company_info    = ?,
                additional_info = ?
            WHERE id = ?
        """, (
            merged_addresses,
            merged_phones,
            merged_websites,
            merged_company_info,
            merged_additional,
            company_id,
        ))
        print(f"  Merged into existing company '{name}' (id={company_id})")

    else:
        cursor.execute("""
            INSERT INTO companies (name, addresses, phones, websites, company_info, additional_info, source_folder)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            name or company.get("name"),
            normalize(company.get("addresses")),
            normalize(company.get("phones")),
            normalize(company.get("websites")),
            company.get("company_info"),
            company.get("additional_info"),
            source_folder,
        ))
        company_id = cursor.lastrowid
        print(f"  Inserted new company '{name}' (id={company_id})")

    conn.commit()
    conn.close()
    return company_id


# Saves people to DB (Also updates)

def save_people(db_path: str, company_id: int | None, data: dict):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    for person in data.get("people", []):
        name = (person.get("name") or "").strip()
        if not name:
            continue

        cursor.execute(
            "SELECT * FROM people WHERE company_id = ? AND TRIM(LOWER(name)) = ?",
            (company_id, name.lower())
        )
        existing = cursor.fetchone()

        if existing:
            merged_phones = _merge_json_lists(existing["phones"], person.get("phones"))
            merged_emails = _merge_json_lists(existing["emails"], person.get("emails"))
            merged_role   = _merge_text(existing["role"], person.get("role"))

            cursor.execute("""
                UPDATE people
                SET phones = ?,
                    emails = ?,
                    role   = ?
                WHERE id = ?
            """, (merged_phones, merged_emails, merged_role, existing["id"]))

        else:
            cursor.execute("""
                INSERT INTO people (company_id, name, role, phones, emails)
                VALUES (?, ?, ?, ?, ?)
            """, (
                company_id,
                name,
                person.get("role"),
                normalize(person.get("phones")),
                normalize(person.get("emails")),
            ))

    conn.commit()
    conn.close()


# Assign categories

def save_categories(db_path: str, company_id: int, categories: list[str]):
    if not categories:
        return

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute(
        "DELETE FROM company_categories WHERE company_id = ?",
        (company_id,)
    )
    cursor.executemany(
        "INSERT INTO company_categories (company_id, category) VALUES (?, ?)",
        [(company_id, cat) for cat in categories]
    )

    conn.commit()
    conn.close()


# Shows categories asscocitaed with a company

def get_categories(db_path: str, company_id: int) -> list[str]:
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT category FROM company_categories WHERE company_id = ? ORDER BY category",
        (company_id,)
    )
    rows = cursor.fetchall()
    conn.close()
    return [row[0] for row in rows]


# Shows all existing categories

def get_all_categories(db_path: str) -> list[str]:
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT DISTINCT category FROM company_categories ORDER BY category"
    )
    rows = cursor.fetchall()
    conn.close()
    return [row[0] for row in rows]

def save_flag(db_path: str, company_id: int, reason: str):
    """
    Insert a flag for a company. Skips if an active flag with the same
    reason already exists — safe to call on every pipeline run.
    """
    from datetime import datetime
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id FROM flagged
        WHERE company_id = ? AND reason = ? AND status = 'active'
    """, (company_id, reason))

    if cursor.fetchone() is None:
        cursor.execute("""
            INSERT INTO flagged (company_id, reason, status, created_at)
            VALUES (?, ?, 'active', ?)
        """, (company_id, reason, datetime.now().isoformat()))

    conn.commit()
    conn.close()


def resolve_flag(db_path: str, company_id: int, reason: str, status: str):
    """
    Update all active flags for a company+reason to the given status.
    status should be 'resolved' or 'dismissed'.
    """
    from datetime import datetime
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute("""
        UPDATE flagged
        SET status = ?, resolved_at = ?
        WHERE company_id = ? AND reason = ? AND status = 'active'
    """, (status, datetime.now().isoformat(), company_id, reason))

    conn.commit()
    conn.close()


def get_flagged(db_path: str) -> list:
    """
    Return all active flagged companies joined with company name
    and their raw products from enrichment.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
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
