# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

פודי (Foodie) — a **single-user** Hebrew/English Telegram nutrition assistant for Ester. Logs meals, reads nutrition labels and dish photos, tracks exercise from fitness screenshots, answers free-text questions, and sends scheduled reminders/summaries. All messages from any Telegram user other than `ALLOWED_TELEGRAM_USER_ID` are silently ignored.

For a detailed reference (schema, flows, prompts, deployment), see `docs/IMPLEMENTATION.md` — it is the source of truth for design decisions.

## Commands

```bash
# Install
pip install -r requirements.txt

# Initialize DB schema (creates tables on the Supabase DB in DATABASE_URL)
python scripts/init_db.py

# Run the bot locally (long-polling)
python src/main.py
```

Required env vars (loaded via `python-dotenv` from `.env`): `TELEGRAM_BOT_TOKEN`, `ALLOWED_TELEGRAM_USER_ID`, `ANTHROPIC_API_KEY`, `DATABASE_URL` (Supabase pooler, port 6543), optional `USDA_API_KEY`.

There is no test suite and no linter configured.

### Deploy
Production runs on an Oracle Cloud VM under systemd (`nutritional-bot.service`). To push an update:
```powershell
scp -i "C:\Users\Ester Hafif\Downloads\ssh-key-2026-04-07.key" -r "C:\projects\nutritional-assistant\src" ubuntu@129.159.155.24:~/nutritional-assistant/
```
```bash
sudo systemctl restart nutritional-bot
journalctl -u nutritional-bot -f
```

## Architecture

**Entry point** `src/main.py` wires handlers and starts APScheduler on `post_init`:
- `filters.PHOTO` → `photo_handler.handle_photo`
- `filters.TEXT` → `route_text` (custom router)
- `CallbackQueryHandler` → `route_callback` (dispatches to steady_meal, meal, then photo callbacks)

**Text routing** (`route_text` in `main.py`) is order-sensitive:
1. If `context.user_data["awaiting_edit_grams"]` is set → `handle_edit_grams_reply` (gram-edit continuation, any flow)
2. If `context.user_data["awaiting_product_name"]` is set → `handle_product_name_reply` (photo label flow continuation)
3. If `context.user_data["awaiting_steady_meal_name"]` is set → `handle_steady_meal_name_reply` (steady meal naming step)
4. Else if `is_steady_meal_creation(text)` matches the `"ארוחה קבועה"` prefix → `handle_steady_meal_creation`
5. Else if `is_structured_meal_log(text)` matches the Hebrew category-header format → `handle_meal_log`
6. Else → `handle_query` (free-text Q&A)

All flows carry conversational state via `user_data`, not `ConversationHandler`.

**Project structure:**
```
src/
├── main.py
├── config.py
├── ai/
│   ├── claude_client.py
│   └── prompts.py
├── bot/
│   ├── handlers/
│   │   ├── meal_handler.py        # Structured meal log: parse → preview → confirm
│   │   ├── photo_handler.py       # Photo: label / dish / exercise / other
│   │   ├── query_handler.py       # Free-text Q&A + summary routing + meal extraction
│   │   ├── steady_meal_handler.py # "ארוחה קבועה" creation flow
│   │   ├── edit_handler.py        # Cross-flow gram-quantity editing
│   │   └── unknown_handler.py     # Fallback help message
│   └── utils/
│       ├── formatters.py          # Progress bars, summary formatting
│       ├── keyboards.py           # Inline keyboards
│       └── time_category.py       # Map current time → default meal category
├── database/
│   ├── models.py
│   ├── db.py
│   └── queries.py
├── external_apis/
│   ├── __init__.py                # lookup_food() — the full 5-step chain
│   ├── open_food_facts.py
│   └── usda_fdc.py
└── scheduler/
    └── tasks.py
```

**Food lookup chain** — every food string resolved in `src/external_apis/__init__.py::lookup_food()` walks a fallback chain:

| Step | Source | Confidence |
|------|--------|-----------|
| 0 | Steady meal (`food_db_items` where `source='steady_meal'`) | 1.0 |
| 1 | Local `food_db_items` (fuzzy ILIKE) | 1.0 |
| 2 | Open Food Facts | 0.95, cached |
| 3 | USDA FoodData Central | 0.95, cached |
| 4 | Claude Haiku estimate | 0.7, `source='claude_estimated'` |

Step 0 checks the original full input string before quantity extraction so steady meal names are found by their exact trigger phrase.

**Q&A flow** — `query_handler` routes by keyword before the normal Q&A path:
- Weekly summary keywords (`שבועי`, `weekly`) → `generate_weekly_summary` (Sonnet 4.6)
- Daily summary keywords (`סיכום`, `summarize`, `summary`, `סכמי`, `תסכמי`) → `generate_daily_summary` (Sonnet 4.6)
- Otherwise → `answer_question` (Sonnet 4.6), then a second Haiku pass (`system_prompt_extract_meals`) silently extracts and logs any food items mentioned, deduplicated against today's existing rows. The Q&A exchange is appended to `conversation_history` (pruned to last 200 rows).

**Photo flow** — `photo_handler` uses Claude **Sonnet 4.6** (vision) to classify the image into four types: `label`, `dish`, `exercise`, `other`. Exercise screenshots extract today's activities and insert into `exercise_log`. Everything except vision classification uses Claude **Haiku 4.5**. A photo caption always overrides the AI-detected product/dish name.

**Steady meal feature** — "ארוחה קבועה" creates a named, reusable combination of ingredients stored in `food_db_items` with `source='steady_meal'`. Components are resolved through the lookup chain, with fuzzy "did you mean?" prompts for Claude-estimated items. Gram quantities per component can be edited before saving.

**Gram-quantity editing** — `edit_handler.py` is a cross-cutting handler that serves label, dish, meal-log, and steady-meal flows. State is stored in `user_data["edit_context"] = {"flow": "label"|"dish"|"meal"|"steady", "item_idx": N}`.

**Scheduler** (`src/scheduler/tasks.py`) — three APScheduler cron jobs in `Asia/Jerusalem`:
- 09:00 morning reminder — **skipped** if yesterday already has all required meals
- 21:00 evening summary — **skipped** unless today has all required meals; generates a summary via Claude Sonnet 4.6
- Saturday 21:01 weekly summary — always sends; covers Sunday–Saturday via `generate_weekly_summary` (Sonnet 4.6)

A day is "fully logged" iff the three required categories (`בוקר`, `צהריים`, `ערב`) each have ≥1 item. `ביניים` and `אחר הצהריים` are optional.

**Database** — SQLAlchemy 2.0 ORM over Postgres (Supabase). Four tables: `food_db_items`, `meals_log`, `exercise_log`, `conversation_history`. All query functions live in `src/database/queries.py`; handlers must not build SQL inline.

**Prompts** — all Claude system prompts live in `src/ai/prompts.py` and inject today's date + the hardcoded `USER_PROFILE` from `config.py`. Every prompt instructs feminine Hebrew grammar (לשון נקבה) and addresses the user as Ester. The calorie target (1500 kcal) is a **soft limit** — prompts explicitly forbid guilt-tripping. Exercise-aware prompts (`system_prompt_qa`, `system_prompt_daily_summary`, `system_prompt_weekly_summary`) inject a formatted exercise context string and use an adjusted calorie target (`1500 + exercise_kcal`).

**Models used:**
- Claude Sonnet 4.6 — photo analysis, Q&A, daily summary, weekly summary
- Claude Haiku 4.5 — meal parsing, meal extraction from Q&A

## Conventions specific to this repo

- `src/main.py` inserts `src/` into `sys.path`, so intra-project imports are written as `from bot.handlers...`, `from database...`, etc. — not `from src.bot...`. Preserve this when adding new modules.
- Meal categories are Hebrew string literals defined in `config.py` (`MEAL_CATEGORIES`, `REQUIRED_MEAL_CATEGORIES`). Never hardcode them elsewhere.
- Confidence scores have fixed meanings: 1.0 exact label/DB, 0.95 API, 0.75 Q&A extraction, 0.7 Claude estimate. Match these when adding new sources.
- `exercise_log` rows are deduplicated by `(exercise_date, exercise_time, activity)` via upsert — do not insert duplicates manually.
