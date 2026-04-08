import asyncio
import json
import logging
import anthropic

logger = logging.getLogger(__name__)
from config import ANTHROPIC_API_KEY
from ai.prompts import (
    system_prompt_meal_parsing,
    system_prompt_label_extraction,
    system_prompt_qa,
    system_prompt_daily_summary,
    system_prompt_extract_meals,
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


def _extract_json_object(raw: str) -> str:
    """Extract the first complete JSON object from a string, ignoring surrounding prose."""
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("No JSON object found in response")
    return raw[start:end + 1]


def _extract_json_array(raw: str) -> str:
    """Extract the first complete JSON array from a string, ignoring surrounding prose."""
    start = raw.find("[")
    end = raw.rfind("]")
    if start == -1 or end == -1 or end < start:
        raise ValueError("No JSON array found in response")
    return raw[start:end + 1]


async def parse_meal_text(text: str) -> list[dict]:
    today = date.today()
    system = system_prompt_meal_parsing(today)
    raw = await asyncio.to_thread(_create_text_message, system, text, HAIKU)
    try:
        return json.loads(_extract_json_array(raw))
    except Exception:
        logger.error("parse_meal_text failed to parse JSON. Raw: %s", raw[:500])
        return []


async def extract_label_from_image(image_bytes: bytes, media_type: str = "image/jpeg") -> dict:
    system = system_prompt_label_extraction()
    raw = await asyncio.to_thread(_create_vision_message, system, image_bytes, media_type)
    try:
        result = json.loads(_extract_json_object(raw))
        result.setdefault("unreadable_fields", [])
        result.setdefault("per_serving", None)
        result.setdefault("per_100g", None)
        return result
    except Exception:
        logger.error("extract_label_from_image failed to parse JSON. Raw: %s", raw[:500])
        return {"error": "Could not parse label response", "raw": raw}


async def answer_question(question: str, meal_history: str) -> str:
    today = date.today()
    system = system_prompt_qa(today, meal_history)
    return await asyncio.to_thread(_create_text_message, system, question, HAIKU)


async def extract_meals_from_conversation(conversation: str) -> list[dict]:
    """Extract and return any food items with nutrition data from a Q&A conversation."""
    today = date.today()
    system = system_prompt_extract_meals(today)
    raw = await asyncio.to_thread(_create_text_message, system, conversation, HAIKU)
    try:
        return json.loads(_extract_json_array(raw))
    except Exception:
        logger.error("extract_meals_from_conversation failed to parse JSON. Raw: %s", raw[:500])
        return []


async def generate_daily_summary(totals: dict) -> str:
    today = date.today()
    system = system_prompt_daily_summary(today)
    user_text = f"Today's nutrition totals: {json.dumps(totals, ensure_ascii=False)}"
    return await asyncio.to_thread(_create_text_message, system, user_text, HAIKU)
