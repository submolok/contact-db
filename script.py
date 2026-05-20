import base64
import time
from pathlib import Path
from openai import OpenAI
import db_addition as newdb
import json
import sys
import os
from datetime import datetime

from dotenv import load_dotenv
load_dotenv()

DB_PATH = str(Path(__file__).parent / "contacts.db")


# TODO: move these to environment variables or a config file in production code

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}

# OCR funtion to read business card and extract company and people info

def read_business_card(image_paths: list) -> str:
    content = []

    for image_path in image_paths:
        with open(image_path, "rb") as f:
            image_data = base64.b64encode(f.read()).decode("utf-8")
        ext = image_path.suffix.lower()
        mime_map = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}
        mime = mime_map[ext]
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:{mime};base64,{image_data}"}
        })

    content.append({
        "type": "text",
        "text": """These images are business cards from employees of the same company. 
Some images are fronts (contain a person's name and contact details), 
some are backs (contain company address, phone, or product info).
Extract and return a JSON object with two keys:
- "company": object with fields: name, addresses (list), websites (list), phones (list), company_info, additional_info
- "people": list of objects each with: name, role, phones (list), emails (list)

Backs should contribute to the company record, not to individual people.
If a field is not present, set it to null.
Return only JSON, no explanation."""
    })

    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": content}]
    )

    raw = response.choices[0].message.content
    clean = raw.replace("```json", "").replace("```", "").strip()
    return clean

# Parsing function to extract details from OCR output

def parse_business_card(raw_text: str) -> str:
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {
                "role": "user",
                "content": f"""You are parsing raw text extracted from a business card.
Extract the following fields:
- name: person's full name
- role: their job title only, not the company division or business unit
- company: the primary company name
- email: all email addresses as a list
- phone: all phone numbers as a list (include mobile, office, any others)
- fax: fax numbers as a list, if any
- address: primary address as a string
- secondary_address: any additional office/branch addresses as a string, if any
- website: all websites as a list
- company_info: anything that describes what the company does, its products, services, industries, slogans, or group affiliations
- other: anything that genuinely doesn't fit any of the above

If a field is not present, set it to null.
Return only JSON, no explanation.

Raw text:
{raw_text}"""
            }
        ]
    )
    return response.choices[0].message.content

# Logging function for GPT responses

def log_raw(run_id: str, label: str, content: str):
    logs_dir = Path(__file__).parent / "logs"
    logs_dir.mkdir(exist_ok=True)
    filename = logs_dir / f"{run_id}_ocr.txt"
    with open(filename, "a", encoding="utf-8") as f:  # "a" = append
        f.write(f"\n{'='*60}\n{label}\n{'='*60}\n")
        f.write(content)
        f.write("\n")

# Funtion to handle folder structure and process cards

def process_folder(folder_path: str):
    folder = Path(folder_path)
    newdb.init_db(DB_PATH)
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")

    # group images by their immediate parent folder (company folder)
    groups = {}
    for image in sorted(folder.rglob("*")):
        if image.suffix.lower() in SUPPORTED_EXTENSIONS:
            parent = image.parent
            if parent not in groups:
                groups[parent] = []
            groups[parent].append(image)

    print(f"Found {len(groups)} company folders. Processing...\n")

    for i, (company_folder, images) in enumerate(groups.items(), 1):
        print(f"[{i}/{len(groups)}] {company_folder.name} — {len(images)} image(s)")
        try:
            raw_json = read_business_card(images)
            log_raw(run_id, f"ocr_{company_folder.name}", raw_json)
            data = json.loads(raw_json)
            company_id = newdb.save_company(DB_PATH, data, str(company_folder))
            newdb.save_people(DB_PATH, company_id, data)
            print(f"  Saved company + {len(data.get('people', []))} people.\n")
        except Exception as e:
            print(f"  ERROR: {e}\n")
        time.sleep(2)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        process_folder(sys.argv[1])
    else:
        folder_path = input("Folder path: ").strip()
        process_folder(folder_path)