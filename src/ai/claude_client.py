import asyncio
import json
import anthropic
from config import ANTHROPIC_API_KEY
from ai.prompts import (
    system_prompt_meal_parsing,
    system_prompt_label_extraction,
    system_prompt_qa,
    system_prompt_daily_summary,
)
from datetime import date

_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
HAIKU = "claude-haiku-4-5-20251001"
SONNET = "claude-sonnet-4-6"


def _create_text_message(system: str, user_text: str, model: str) -> str:
    response = _client.messages.create(
        model=model,
        max_tokens=1024,
        system=system,
        messages=[{"role": "user", "content": user_text}],
    )
    return response.content[0].text


def _create_vision_message(system: str, image_bytes: bytes, media_type: str) -> str:
    import base64
    image_data = base64.standard_b64encode(image_bytes).decode("utf-8")
    response = _client.messages.create(
        model=SONNET,
        max_tokens=1024,
        system=system,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": media_type, "data": image_data},
                },
                {"type": "text", "text": "Extract the nutritional information from this label."},
            ],
        }],
    )
    return response.content[0].text


def _strip_code_fence(raw: str) -> str:
    clean = raw.strip()
    if clean.startswith("```"):
        parts = clean.split("```")
        if len(parts) >= 2:
            clean = parts[1]
            if clean.startswith("json"):
                clean = clean[4:]
    return clean.strip()


async def parse_meal_text(text: str) -> list[dict]:
    today = date.today()
    system = system_prompt_meal_parsing(today)
    raw = await asyncio.to_thread(_create_text_message, system, text, HAIKU)
    try:
        return json.loads(_strip_code_fence(raw))
    except Exception:
        return []


async def extract_label_from_image(image_bytes: bytes, media_type: str = "image/jpeg") -> dict:
    system = system_prompt_label_extraction()
    raw = await asyncio.to_thread(_create_vision_message, system, image_bytes, media_type)
    try:
        lines = raw.strip().split("\n")
        json_lines = []
        unreadable_fields = []
        for line in lines:
            if line.startswith("UNREADABLE:"):
                unreadable_text = line[len("UNREADABLE:"):].strip()
                unreadable_fields = [f.strip() for f in unreadable_text.split(",") if f.strip()]
            else:
                json_lines.append(line)
        json_str = _strip_code_fence("\n".join(json_lines))
        result = json.loads(json_str)
        if unreadable_fields:
            result["unreadable_fields"] = unreadable_fields
        return result
    except Exception:
        return {"error": "Could not parse label response", "raw": raw}


async def answer_question(question: str, meal_history: str) -> str:
    today = date.today()
    system = system_prompt_qa(today, meal_history)
    return await asyncio.to_thread(_create_text_message, system, question, HAIKU)


async def generate_daily_summary(totals: dict) -> str:
    today = date.today()
    system = system_prompt_daily_summary(today)
    user_text = f"Today's nutrition totals: {json.dumps(totals, ensure_ascii=False)}"
    return await asyncio.to_thread(_create_text_message, system, user_text, HAIKU)
