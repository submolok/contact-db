"""
normalizer.py
-------------
Reads the enrichment table, classifies each company's raw GPT products
into standardized categories, and populates the company_categories table.

Usage:
    python normalizer.py --db path/to/your.db
    python normalizer.py --db path/to/your.db --dry-run   # print matches, don't write
"""

import argparse
import json
import re
import sqlite3
from collections import defaultdict
from db_addition import save_categories, save_flag


# ---------------------------------------------------------------------------
# Category keyword map
# Each key is the canonical category name.
# Values are lists of lowercase substrings/phrases matched against product strings.
# Longer phrases are checked before shorter ones (sorted at build time).
# ---------------------------------------------------------------------------

CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "Earthmoving Equipment": [
        "excavator", "excavators", "backhoe", "dozer", "bulldozer",
        "wheel loader", "skid steer", "motor grader", "grader",
        "dump truck", "dumptruck", "dumptrucks", "articulated dump",
        "articulated truck", "off-highway truck", "off highway truck",
        "scraper", "track loader", "tracked loader", "track type tractor",
        "earthmoving", "earth moving", "loaders", "compactor", "compactors",
        "heavy equipment", "industrial machinery",
    ],
    "Concrete & Paving Equipment": [
        "batching plant", "batch plant", "batch-mix plant", "concrete pump",
        "boom pump", "transit mixer", "concrete mixer", "slipform",
        "slip-form", "slip form", "concrete paver", "asphalt plant",
        "asphalt paver", "asphalt finisher", "road roller", "roller",
        "milling machine", "chip spreader", "paver", "reclaimer",
        "stabilizer", "asphalt distributor", "heating solution",
        "refractory",
    ],
    "Lifting & Material Handling": [
        "crane", "cranes", "telehandler", "telescopic handler", "forklift", "forklifts",
        "reach stacker", "reachstacker", "manlift", "aerial lift",
        "aerial work platform", "container handler", "luffing crane",
        "tower crane", "crawler crane", "crawler cranes", "rough terrain crane",
        "all terrain crane", "material handling",
    ],
    "Crushing & Screening": [
        "crusher", "jaw crusher", "cone crusher", "impact crusher",
        "vsi crusher", "stone crusher", "mobile crusher", "screener",
        "screening", "trommel", "vibrating feeder", "hydrocyclone",
        "secondary feeder", "bucket washer",
    ],
    "Drilling & Piling Equipment": [
        "drilling rig", "drill rig", "crawler drill", "dth", "blast hole drill",
        "blasthole drill", "water well drill", "water well drilling",
        "pole drilling", "earth hole drilling", "solar piling", "piling rig",
        "foundation drilling", "exploration drilling", "borehole",
        "rotary drill", "pneumatic crawler drill", "drilling equipment",
    ],
    "Mining Equipment": [
        "mining excavator", "mining excavators", "mining equipment",
        "mining machinery", "mining dump", "mining dumptruck", "mining dumptrucks",
        "mining truck", "hemm", "blasthole drill rig", "bulk material handling",
        "heavy mining", "mining machine", "blast hole drill rig", "mine",
    ],
    "Power Generation & Compressors": [
        "generator", "generators", "genset", "gen-set", "air compressor",
        "air compressors", "compressor", "lighting tower", "lighting towers",
        "light tower", "diesel engine", "natural gas engine", "gas turbine",
        "turbine", "battery energy storage", "solar installation",
        "power backup", "cummins generator", "engine", "engines",
        "transmission line", "substation", "oil & gas pipeline",
        "offshore construction", "offshore vessel",
    ],
    "Road & Infrastructure Construction": [
        "road construction", "highway development", "epc project", "bot project",
        "ham project", "railway project", "railway construction", "power project",
        "infrastructure", "surfacing", "earth work", "earthwork",
        "construction contracting", "construction & contracting",
        "cables", "oil & gas pipeline",
    ],
    "Spare Parts & Components": [
        "spare part", "spare parts", "undercarriage", "bucket tooth",
        "bucket teeth", "adaptor", "adapter", "seal kit", "o-ring", "o ring",
        "swing bearing", "slewing bearing", "hydraulic pump", "engine part",
        "engine parts", "filter kit", "transmission part", "transmission gear",
        "rear axle", "pins", "bushings", "pivot pin", "crankshaft", "piston",
        "cylinder liner", "cylinder head", "locomotive part",
        "replacement part", "aftermarket", "ground engaging tool",
        "reconditioned", "recon assembly", "final drive", "travel motor",
        "swing gearbox", "travel gearbox", "torque converter", "disc carrier",
        "caterpillar", "komatsu", "hitachi", "volvo ce", "volvo penta", "brake component", "clutch component",
        "axle", "radiator", "wheel rim", "suspension", "compressed air system",
        "gear box", "gearbox", "propeller", "maintenance kit",
        "truck interior", "truck exterior", "bus interior", "bus exterior",
        "automotive plastic", "rubber component", "sheet metal component",
        "casting component", "rear-view mirror", "lighting & signalling",
        "construction equipment",
    ],
    "Tyres & Wheels": [
        "tyre", "tire", "tyres", "tires", "otr tyre", "otr tire", "tbr",
        "industrial tyre", "industrial tire", "industrial tires",
        "mining tyre", "mining tire", "agricultural tyre", "agricultural tire",
        "tyre management", "tire management", "tpms",
        "tyre pressure", "tire pressure", "tyre recycling", "tube",
        "pneumatic tire", "tricycle tire",
    ],
    "Hydraulic Systems & Attachments": [
        "hydraulic hose", "hydraulic breaker", "hydraulic attachment",
        "hydraulic filter", "hydraulic filters", "filter element",
        "filter elements", "complete filter", "simplex filter", "duplex filter",
        "self-cleaning filter", "gap-type filter", "beta filter",
        "hydraulic valve", "hydraulic ram", "hydraulic component",
        "hose fitting", "hose protection", "rotary union", "slip ring",
        "slip rings", "filtration system", "hydraulic system",
    ],
    "Lubricants & Fluids": [
        "lubricant", "lubricants", "lubrication", "engine oil", "lube oil",
        "grease", "technical fluid", "technical fluids", "coolant", "valvoline",
    ],
    "Telematics & Machine Control": [
        "telematics", "grade control", "machine control",
        "2d machine", "3d machine", "land survey", "surveying",
        "fleet management", "fleet solution", "on-board scale",
        "weighing system", "route optimization", "live tracking",
        "mobile automation", "oem sensor", "excavator control",
        "joystick", "joysticks", "control station", "encoder", "encoders",
        "pull wire e-stop", "radio remote controller", "radio remote",
    ],
    "Logistics & Transport": [
        "sea freight", "air freight", "land freight", "freight", "logistics",
        "cargo", "customs clearance", "customs broker", "shipping", "haulage",
        "transport", "nvocc", "project cargo", "warehousing", "distribution",
        "forwarding",
    ],
    "Finance & Services": [
        "loan", "finance", "equipment finance", "leasing", "rental",
        "equipment rental", "credit", "deposit", "investment",
        "executive search", "recruitment", "consulting", "legal",
        "accounting", "payroll", "tax", "corporate law",
        "managed service", "it service", "software",
    ],
    "Raw & Construction Materials": [
        "structural steel", "wear-resistant steel", "armor steel",
        "high-strength steel", "strenx", "hardox", "toolox", "armox",
        "duroxite", "greencoat", "steel",
        "pvc", "eva", "poe", "cpvc", "hdpe", "ldpe", "lldpe",
        "cpe impact", "plasticizer", "polymer",
        "cement", "aggregates", "m sand", "concrete product", "bricks",
        "plumbing", "flooring", "adhesive", "waterproofing", "gypsum",
        "raw material", "construction material",
    ],
    "Automobiles & Vehicles": [
        "sedan", "suv", "coupe", "convertible", "hatchback", "pick-up",
        "compact car", "passenger car", "two wheeler", "three wheeler",
        "four wheeler", "automobile", "commercial vehicle",
        "minibus", "toyota crown", "toyota lc", "byd", "hyundai elantra",
        "mitsubishi xpander", "mitsubishi pajero", "li auto", "kia picanto",
    ],
}

# Flat lookup sorted longest-first so longer phrases match before substrings
_KEYWORD_INDEX: list[tuple[str, str]] = sorted(
    [(kw.lower(), cat) for cat, kws in CATEGORY_KEYWORDS.items() for kw in kws],
    key=lambda x: -len(x[0]),
)

# Given the raw JSON string from enrichment.products, return a sorted list of matching category names.

def classify_products(products_json: str | None) -> list[str]:
    if not products_json:
        return []

    try:
        products = json.loads(products_json)
    except (json.JSONDecodeError, TypeError):
        return []

    if not isinstance(products, list):
        return []

    combined = " | ".join(str(p) for p in products if p).lower()

    matched: set[str] = set()
    for keyword, category in _KEYWORD_INDEX:
        if re.search(r'\b' + re.escape(keyword) + r'\b', combined):
            matched.add(category)

    return sorted(matched)

# Runs enrichment

def run(db_path: str, dry_run: bool = False):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("""
        SELECT contact_id, products
        FROM enrichment
        WHERE contact_id IS NOT NULL
    """)
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        print("No enrichment rows found. Have you run the enrichment pipeline yet?")
        return

    stats: dict[str, int] = defaultdict(int)
    unmatched_companies: list[int] = []

    for row in rows:
        company_id = row["contact_id"]
        categories = classify_products(row["products"])

        stats["total"] += 1
        if categories:
            stats["matched"] += 1
        else:
            stats["unmatched"] += 1
            unmatched_companies.append(company_id)

        if dry_run:
            print(f"company_id={company_id:>4}  →  {categories or '(no match)'}")
        else:
            save_categories(db_path, company_id, categories)
            if not categories:
                save_flag(db_path, company_id, "no_category_match")

    print("\n" + "=" * 50)
    print(f"  Total enrichment rows : {stats['total']}")
    print(f"  Matched to categories : {stats['matched']}")
    print(f"  No category match     : {stats['unmatched']}")
    if dry_run:
        print("  DRY RUN — nothing written to DB")
    else:
        print("  company_categories table updated ")
    print("=" * 50)

    if unmatched_companies:
        print(f"\nCompany IDs with no category match ({len(unmatched_companies)}):")
        print("  " + ", ".join(str(i) for i in unmatched_companies))
        print("  These are likely null products or intentionally off-topic companies.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Classify enrichment products into standardized categories."
    )
    parser.add_argument("--db", required=True, help="Path to the SQLite database file")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print classifications without writing to the database",
    )
    args = parser.parse_args()
    run(db_path=args.db, dry_run=args.dry_run)
