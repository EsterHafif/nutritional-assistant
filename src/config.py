import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN: str = os.environ["TELEGRAM_BOT_TOKEN"]
ALLOWED_TELEGRAM_USER_ID: int = int(os.environ["ALLOWED_TELEGRAM_USER_ID"])
ANTHROPIC_API_KEY: str = os.environ["ANTHROPIC_API_KEY"]
DATABASE_URL: str = os.environ["DATABASE_URL"]
USDA_API_KEY: str = os.environ.get("USDA_API_KEY", "")

USER_PROFILE = {
    "name": "Ester",
    "sex": "female",
    "tdee_kcal": 2050,
    "calorie_target": 1500,
    "protein_goal_g": 100,
    "rda": {
        "iron_mg": 18,
        "calcium_mg": 1000,
        "folate_mcg": 400,
        "vitamin_d_mcg": 15,
        "magnesium_mg": 310,
    }
}

MEAL_CATEGORIES = ["בוקר", "ביניים", "צהריים", "אחר הצהריים", "ערב"]
REQUIRED_MEAL_CATEGORIES = ["בוקר", "צהריים", "ערב"]
MORNING_REMINDER_HOUR = 9
EVENING_SUMMARY_HOUR = 21
TIMEZONE = "Asia/Jerusalem"
