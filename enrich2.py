import json
import psycopg2         # replace with sqlite3 if needed
from psycopg2 import extras
from firecrawl import Firecrawl
from openai import OpenAI
from pathlib import Path
from db_addition import save_flag
import os
from datetime import datetime 

from dotenv import load_dotenv
import os

load_dotenv()

# DB_PATH = str(Path(__file__).parent / "contacts.db")

# TODO: move these to environment variables or a config file in production code

firecrawl = Firecrawl(api_key=os.getenv("FIRECRAWL_API_KEY", ""))
openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

def log_raw(run_id: str, label: str, content: str):
    logs_dir = Path(__file__).parent / "logs"
    logs_dir.mkdir(exist_ok=True)
    filename = logs_dir / f"{run_id}_enrichment.txt"
    with open(filename, "a", encoding="utf-8") as f:  # "a" = append
        f.write(f"\n{'='*60}\n{label}\n{'='*60}\n")
        f.write(content)
        f.write("\n")


def get_companies() -> list:
    conn = psycopg2.connect(os.getenv("DATABASE_URL"))
    conn.cursor_factory = extras.RealDictCursor
    cursor = conn.cursor()
    cursor.execute("""
        SELECT c.id, c.name, c.websites, MIN(p.emails) as emails
        FROM companies c
        LEFT JOIN people p ON p.company_id = c.id
        GROUP BY c.id
    """)
    rows = cursor.fetchall()
    conn.close()
    return rows

def extract_domain(email: str) -> str | None:
    if not email:
        return None
    # email field is stored as a JSON list
    try:
        emails = json.loads(email)
        if isinstance(emails, list) and emails:
            email = emails[0]
    except (json.JSONDecodeError, TypeError):
        pass
    if "@" in email:
        return email.split("@")[1].strip()
    return None


def scrape_website(domain: str, run_id: str) -> str | None:
    try:
        clean_domain = domain.replace("https://", "").replace("http://", "").rstrip("/")
        result = firecrawl.scrape(
            f"https://{clean_domain}",
            formats=["markdown"]
        )
        return result.markdown
    except Exception as e:
        log_raw(run_id, f"scrape_failed_{domain}", str(e))
        print(f"  Firecrawl error for {domain}: {e}")
        return None


def enrich_company(website_text: str, run_id: str) -> dict:
    response = openai_client.chat.completions.create(
        model="gpt-4o",
        messages=[{
                    "role": "user",
                    "content": f"""You are building a company profile from website content.
                                Extract the following fields:
                                - primary_industry: broad sector (e.g. Construction, Mining, Agriculture, Oil & Gas, Manufacturing)
                                - sub_industry: more specific segment (e.g. Earthmoving, Concrete, Power Generation)
                                - company_type: one or more of Manufacturer, Dealer, Distributor, Contractor, Rental, Service, Repair, or something else if it doesn't fit these categories 
                                - products: list of specific products or equipment lines they deal in
                                - markets: regions or countries they operate in

                                If a field cannot be determined, set it to null.
                                Return only JSON, no explanation.

                                Website content:
                                {website_text[:6000]}"""
                    }
                ]
    )
    raw = response.choices[0].message.content
    try:
        if not raw:
            log_raw(run_id, f"enrich_gpt", "Empty response from GPT")
            return {}
        log_raw(f"enrich_{run_id}", f"enrich_gpt", raw)
        clean = raw.replace("```json", "").replace("```", "").strip()
        return json.loads(clean)
    except json.JSONDecodeError:
        print("  Failed to parse GPT response as JSON")
        return {}


def save_enrichment(contact_id: int, domain: str | None, data: dict):
    conn = psycopg2.connect(os.getenv("DATABASE_URL"))
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS enrichment (
            id SERIAL PRIMARY KEY ,
            contact_id INTEGER,
            domain TEXT,
            primary_industry TEXT,
            sub_industry TEXT,
            company_type TEXT,
            products TEXT,
            markets TEXT,
            FOREIGN KEY (contact_id) REFERENCES contacts(id)
        )
    """)

    cursor.execute("""
        INSERT INTO enrichment (contact_id, domain, primary_industry, sub_industry, company_type, products, markets)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
    """, (
        contact_id,
        domain,
        data.get("primary_industry"),
        data.get("sub_industry"),
        json.dumps(data.get("company_type")),
        json.dumps(data.get("products")),
        json.dumps(data.get("markets"))
    ))

    conn.commit()
    conn.close()

def is_already_enriched(contact_id: int) -> bool:
    conn = psycopg2.connect(os.getenv("DATABASE_URL"))
    conn.cursor_factory = extras.RealDictCursor
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT id FROM enrichment WHERE contact_id = %s", (contact_id,))
        return cursor.fetchone() is not None
    except Exception:
        return False
    finally:
        conn.close()

def enrich_all(single_id: int = None):
    companies = get_companies()
    if single_id:
        companies = [r for r in companies if r["id"] == single_id]
    print(f"Found {len(companies)} companies to enrich.\n")
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")

    for row in companies:
        contact_id = row["id"]
        company_name = row["name"]

        if is_already_enriched(contact_id):
            print(f"[{contact_id}] {company_name} — already enriched, skipping.")
            continue

        # try websites first
        domain = None
        websites = row["websites"]
        if websites:
            try:
                website_list = json.loads(websites)
                if isinstance(website_list, list) and website_list:
                    domain = website_list[0].replace("https://", "").replace("http://", "").rstrip("/")
            except (json.JSONDecodeError, TypeError):
                pass

        # fall back to email domain
        if not domain:
            domain = extract_domain(row["emails"])

        print(f"[{contact_id}] {company_name}")

        if not domain or domain.lower() == "null":
            log_raw(f"enrich_{run_id}", f"no_domain_{company_name}", f"Company ID {contact_id} — no domain or email found")
            print("  No domain found, saving empty entry.\n")
            save_enrichment(contact_id, None, {})
            save_flag(contact_id, "no_domain")
            continue

        print(f"  Domain: {domain}")

        website_text = scrape_website(domain, run_id)
        if not website_text:
            print("  Could not scrape website, saving empty entry.\n")
            save_enrichment(contact_id, domain, {})
            save_flag(contact_id, "scrape_failed")
            continue

        print("  Scraped. Enriching with GPT...")
        enrichment = enrich_company(website_text, run_id)
        if not enrichment:
            log_raw(f"enrich_{run_id}", f"enrich_parse_failed_{domain}", website_text[:500])
        save_enrichment(contact_id, domain, enrichment)
        if not enrichment:
            save_flag(contact_id, "enrichment_failed")
        print("  Done.\n")

    print("Enrichment complete.")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--single", type=int, help="Enrich a single company by ID")
    args = parser.parse_args()

    if args.single:
        # single enrichment
        companies = [r for r in get_companies() if r["id"] == args.single]
        if not companies:
            print(f"Company ID {args.single} not found.")
        else:
            run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
            # re-use existing enrich logic but force re-enrichment
            conn = psycopg2.connect(os.getenv("DATABASE_URL"))
            conn.cursor_factory = extras.RealDictCursor
            cursor = conn.cursor()
            cursor.execute("DELETE FROM enrichment WHERE contact_id = %s", (args.single,))
            conn.commit()
            conn.close()
            enrich_all(single_id=args.single)
    else:
        # bulk enrichment
        enrich_all()