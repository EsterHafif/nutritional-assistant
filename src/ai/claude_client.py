import asyncio
import json
import logging
import anthropic

logger = logging.getLogger(__name__)
from config import ANTHROPIC_API_KEY
from ai.prompts import (
    system_prompt_meal_parsing,
    system_prompt_image_analysis,
    system_prompt_qa,
    system_prompt_qa_with_tools,
    system_prompt_daily_summary,
    system_prompt_extract_meals,
    system_prompt_weekly_summary,
    format_exercise_context,
)
from database.queries import get_exercise_for_date
from datetime import date

_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
HAIKU = "claude-haiku-4-5-20251001"
SONNET = "claude-sonnet-4-6"


def _create_text_message(system: str, user_text: str, model: str, max_tokens: int = 1024) -> str:
    response = _client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user_text}],
    )
    return response.content[0].text


def _create_vision_message(system: str, image_bytes: bytes, media_type: str, user_text: str) -> str:
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
                {"type": "text", "text": user_text},
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


async def analyze_food_image(image_bytes: bytes, caption: str = "", media_type: str = "image/jpeg") -> dict:
    system = system_prompt_image_analysis()
    user_text = (
        f"Caption from Ester: {caption}\n\nAnalyze the image."
        if caption
        else "Analyze the image."
    )
    raw = await asyncio.to_thread(_create_vision_message, system, image_bytes, media_type, user_text)
    try:
        result = json.loads(_extract_json_object(raw))
        if result.get("image_type") == "label":
            result.setdefault("unreadable_fields", [])
            result.setdefault("per_serving", None)
            result.setdefault("per_100g", None)
        return result
    except Exception:
        logger.error("analyze_food_image failed to parse JSON. Raw: %s", raw[:500])
        return {"error": "Could not parse image analysis", "raw": raw}


def _todays_exercise_summary() -> dict:
    try:
        return get_exercise_for_date(date.today())
    except Exception as e:
        logger.error("failed to load exercise context: %s", e)
        return {}


async def answer_question(question: str, meal_history: str) -> str:
    today = date.today()
    ex = _todays_exercise_summary()
    system = system_prompt_qa(
        today, meal_history,
        exercise_context=format_exercise_context(ex),
        exercise_kcal=ex.get("total_kcal") or 0,
    )
    return await asyncio.to_thread(_create_text_message, system, question, SONNET)


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


TOOL_DEFINITIONS = [
    {
        "name": "add_meal",
        "description": "Add a new meal entry to today's log (or another date). Use lookup_food first to get nutrition data.",
        "input_schema": {
            "type": "object",
            "properties": {
                "meal_name": {"type": "string", "description": "Name of the food item"},
                "meal_category": {"type": "string", "enum": ["בוקר", "ביניים", "צהריים", "אחר הצהריים", "ערב"],
                                  "description": "Meal category"},
                "meal_date": {"type": "string", "description": "YYYY-MM-DD, defaults to today"},
                "calories": {"type": "number"},
                "protein_g": {"type": "number"},
                "carbs_g": {"type": "number"},
                "fat_g": {"type": "number"},
                "fiber_g": {"type": "number"},
                "sugar_g": {"type": "number"},
                "calcium_mg": {"type": "number"},
                "iron_mg": {"type": "number"},
                "magnesium_mg": {"type": "number"},
            },
            "required": ["meal_name", "meal_category"],
        },
    },
    {
        "name": "update_meal",
        "description": "Update an existing meal entry by ID. Only include fields you want to change.",
        "input_schema": {
            "type": "object",
            "properties": {
                "meal_id": {"type": "integer", "description": "The meal ID from the context"},
                "meal_name": {"type": "string"},
                "meal_category": {"type": "string", "enum": ["בוקר", "ביניים", "צהריים", "אחר הצהריים", "ערב"]},
                "calories": {"type": "number"},
                "protein_g": {"type": "number"},
                "carbs_g": {"type": "number"},
                "fat_g": {"type": "number"},
                "fiber_g": {"type": "number"},
                "sugar_g": {"type": "number"},
                "calcium_mg": {"type": "number"},
                "iron_mg": {"type": "number"},
                "magnesium_mg": {"type": "number"},
            },
            "required": ["meal_id"],
        },
    },
    {
        "name": "delete_meal",
        "description": "Delete a meal entry by ID.",
        "input_schema": {
            "type": "object",
            "properties": {
                "meal_id": {"type": "integer", "description": "The meal ID to delete"},
            },
            "required": ["meal_id"],
        },
    },
    {
        "name": "lookup_food",
        "description": "Look up nutrition data for a food item by name. Returns calories, protein, carbs, fat, etc. Use this before add_meal when you need accurate nutrition values.",
        "input_schema": {
            "type": "object",
            "properties": {
                "food_name": {"type": "string", "description": "Food name to look up (Hebrew or English)"},
            },
            "required": ["food_name"],
        },
    },
    {
        "name": "add_exercise",
        "description": "Add a manual exercise entry.",
        "input_schema": {
            "type": "object",
            "properties": {
                "activity": {"type": "string", "description": "Activity name in Hebrew"},
                "duration_min": {"type": "integer", "description": "Duration in minutes"},
                "calories": {"type": "integer", "description": "Calories burned"},
                "exercise_date": {"type": "string", "description": "YYYY-MM-DD, defaults to today"},
                "exercise_time": {"type": "string", "description": "HH:MM format"},
            },
            "required": ["activity"],
        },
    },
    {
        "name": "delete_exercise",
        "description": "Delete an exercise entry by ID.",
        "input_schema": {
            "type": "object",
            "properties": {
                "exercise_id": {"type": "integer", "description": "The exercise ID to delete"},
            },
            "required": ["exercise_id"],
        },
    },
    {
        "name": "delete_steady_meal",
        "description": "Delete a steady meal (ארוחה קבועה) by ID from the food database.",
        "input_schema": {
            "type": "object",
            "properties": {
                "steady_meal_id": {"type": "integer", "description": "The steady meal ID to delete"},
            },
            "required": ["steady_meal_id"],
        },
    },
]


def _sync_tool_use_loop(system: str, user_text: str, tool_executor, max_rounds: int = 5) -> str:
    """Run a tool-use conversation loop with Claude. Returns final text response."""
    messages = [{"role": "user", "content": user_text}]

    text_parts = []
    for _ in range(max_rounds):
        response = _client.messages.create(
            model=SONNET,
            max_tokens=1024,
            system=system,
            tools=TOOL_DEFINITIONS,
            messages=messages,
        )

        text_parts = []
        tool_uses = []
        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_uses.append(block)

        if not tool_uses:
            return "\n".join(text_parts) if text_parts else ""

        messages.append({"role": "assistant", "content": response.content})

        tool_results = []
        for tool_use in tool_uses:
            result = tool_executor(tool_use.name, tool_use.input)
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tool_use.id,
                "content": json.dumps(result, ensure_ascii=False) if isinstance(result, (dict, list)) else str(result),
            })
        messages.append({"role": "user", "content": tool_results})

    return "\n".join(text_parts) if text_parts else ""


async def answer_with_tools(question: str, meal_context: str, tool_executor) -> str:
    """Answer a question using Claude with tool use for CRUD operations."""
    today = date.today()
    ex = _todays_exercise_summary()
    system = system_prompt_qa_with_tools(
        today, meal_context,
        exercise_context=format_exercise_context(ex),
        exercise_kcal=ex.get("total_kcal") or 0,
    )
    return await asyncio.to_thread(_sync_tool_use_loop, system, question, tool_executor)


async def generate_weekly_summary(weekly_data: dict, week_start, week_end, is_partial: bool = False) -> str:
    system = system_prompt_weekly_summary(week_start, week_end, is_partial=is_partial)
    user_text = json.dumps(weekly_data, ensure_ascii=False)
    return await asyncio.to_thread(_create_text_message, system, user_text, SONNET, 2048)


async def generate_daily_summary(totals: dict, meals_by_category: dict | None = None) -> str:
    today = date.today()
    ex = _todays_exercise_summary()
    system = system_prompt_daily_summary(
        today,
        exercise_context=format_exercise_context(ex),
        exercise_kcal=ex.get("total_kcal") or 0,
    )
    payload = {"totals": totals}
    if meals_by_category:
        payload["meals_by_category"] = meals_by_category
    user_text = json.dumps(payload, ensure_ascii=False)
    return await asyncio.to_thread(_create_text_message, system, user_text, SONNET)
