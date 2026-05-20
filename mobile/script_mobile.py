import base64
import json
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

client = OpenAI()

SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
MIME_MAP = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}


def log_raw(run_id: str, label: str, content: str):
    logs_dir = Path(__file__).parent / "logs"
    logs_dir.mkdir(exist_ok=True)
    filename = logs_dir / f"{run_id}_mobile.txt"
    with open(filename, "a", encoding="utf-8") as f:
        f.write(f"\n{'='*60}\n{label}\n{'='*60}\n")
        f.write(content)
        f.write("\n")


def encode_image(image_bytes: bytes, mime_type: str) -> dict:
    """Encode raw image bytes into a GPT content block."""
    image_data = base64.b64encode(image_bytes).decode("utf-8")
    return {
        "type": "image_url",
        "image_url": {"url": f"data:{mime_type};base64,{image_data}"}
    }


def read_business_card_bytes(images: list[tuple[bytes, str]], run_id: str) -> dict:
    """
    Extract structured data from 1 or 2 business card images.

    Args:
        images: list of (image_bytes, mime_type) tuples — front first, back second
        run_id: logging identifier

    Returns:
        Parsed dict with keys: company, people
    """
    content = []

    for image_bytes, mime_type in images:
        content.append(encode_image(image_bytes, mime_type))

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

    if not raw:
        log_raw(run_id, "mobile_gpt_empty", "GPT returned empty response")
        return {}

    log_raw(run_id, "mobile_gpt_response", raw)

    clean = raw.replace("```json", "").replace("```", "").strip()
    try:
        return json.loads(clean)
    except json.JSONDecodeError:
        log_raw(run_id, "mobile_parse_failed", clean)
        return {}


def get_mime_type(filename: str) -> str | None:
    """Get MIME type from filename extension."""
    ext = Path(filename).suffix.lower()
    return MIME_MAP.get(ext)


def make_run_id() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")
