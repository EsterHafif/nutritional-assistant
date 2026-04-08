# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

פודי (Foodie) — a **single-user** Hebrew/English Telegram nutrition assistant for Ester. Logs meals, reads nutrition labels from photos, answers free-text questions, and sends scheduled reminders/summaries. All messages from any Telegram user other than `ALLOWED_TELEGRAM_USER_ID` are silently ignored.

For a detailed reference (schema, flows, prompts, deployment), see `plans/IMPLEMENTATION.md` — it is the source of truth for design decisions.

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
scp -i "<ssh key>" -r "C:\projects\nutritional-assistant\src" ubuntu@129.159.155.24:~/nutritional-assistant/
```
```bash
sudo systemctl restart nutritional-bot
journalctl -u nutritional-bot -f
```

## Architecture

**Entry point** `src/main.py` wires three `python-telegram-bot` v20 handlers and starts APScheduler on `post_init`:
- `filters.PHOTO` → `photo_handler.handle_photo`
- `filters.TEXT` → `route_text` (custom router)
- `CallbackQueryHandler` → `photo_handler.handle_photo_callback`

**Text routing** (`route_text` in `main.py`) is order-sensitive:
1. If `context.user_data["awaiting_product_name"]` is set → `handle_product_name_reply` (continuation of the photo flow).
2. Else if `is_structured_meal_log(text)` matches the Hebrew category-header format → `handle_meal_log`.
3. Else → `handle_query` (free-text Q&A).

Because of (1), the photo flow carries conversational state across messages via `user_data`, not via `ConversationHandler`.

**Food lookup chain** — every food string resolved in `src/external_apis/__init__.py::lookup_food()` walks a fallback chain, each step writing confidence into the meal row:
1. Local `food_db_items` (fuzzy ILIKE) — `1.0`
2. Open Food Facts — `0.95`, cached into `food_db_items`
3. USDA FoodData Central — `0.95`, cached into `food_db_items`
4. Claude Haiku estimate — `0.7`, source = `claude_estimated`

**Q&A side-effect** — `query_handler` does more than answer: after Claude Haiku responds, it calls a second Claude Haiku pass (`system_prompt_extract_meals`) to silently pull any food items mentioned in the exchange and insert them into `meals_log`, deduplicated by name against today's existing rows. The Q&A exchange itself is appended to `conversation_history`, which is pruned to the last 200 rows on each insert.

**Photo flow** — `photo_handler` uses Claude **Sonnet 4.6** (vision) for label extraction; everything else uses Claude **Haiku 4.5**. The label prompt is explicitly told to read the per-serving column (בגביע/במנה) on Israeli labels, never the per-100g column. A photo caption, if present, always overrides the label-detected `product_name`. The callback flow branches: product name known → "log as meal?" → category picker → saves to both `food_db_items` and `meals_log`; not a meal → optional save to `food_db_items` only.

**Scheduler** (`src/scheduler/tasks.py`) — two APScheduler cron jobs in `Asia/Jerusalem`:
- 09:00 morning reminder — **skipped** if yesterday already has all required meals (`בוקר`, `צהריים`, `ערב`).
- 21:00 evening summary — **skipped** unless today has all required meals; otherwise generates a warm Hebrew summary via Claude Haiku.

A day is "fully logged" iff the three required categories each have ≥1 item. `ביניים` and `אחר הצהריים` are optional.

**Database** — SQLAlchemy 2.0 ORM over Postgres (Supabase). Three tables: `food_db_items` (nutritional data per 100g, with JSONB `data` holding the full upstream API response), `meals_log` (one row per food item per meal, FK to `food_db_items`), `conversation_history` (capped 200 rows). All query functions live in `src/database/queries.py`; handlers should not build SQL inline.

**Prompts** — all Claude system prompts live in `src/ai/prompts.py` and inject today's date + the hardcoded `USER_PROFILE` from `config.py`. Every prompt instructs feminine Hebrew grammar (לשון נקבה) and addresses the user as Ester. The calorie target (1500 kcal) is a **soft limit** — prompts explicitly forbid guilt-tripping.

## Conventions specific to this repo

- `src/main.py` inserts `src/` into `sys.path`, so intra-project imports are written as `from bot.handlers...`, `from database...`, etc. — not `from src.bot...`. Preserve this when adding new modules.
- Meal categories are Hebrew string literals defined in `config.py` (`MEAL_CATEGORIES`, `REQUIRED_MEAL_CATEGORIES`). Never hardcode them elsewhere.
- Confidence scores have fixed meanings (1.0 exact label, 0.95 API, 0.75 Q&A extraction, 0.7 Claude estimate) — match these when adding new sources.
