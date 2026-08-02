"""
Day 4 + 12: Weekly menu planner with agentic validation loop.

Day 12 additions:
- Reads menu_history to enforce R007 (no repeats in 10-day window) across weeks
- History-aware validator catches cross-week duplicates
- Immutability check protects existing weeks unless --force

Multi-model, self-critique + auto-retry pattern.
"""

import os
import sys
import json
import time
from datetime import date, datetime, timedelta
from dotenv import load_dotenv
from google import genai
from google.genai.errors import ServerError, ClientError
from load_data import get_sheet, load_tab

load_dotenv()

MODEL_NAME = "gemini-2.5-flash"
MAX_VALIDATION_ATTEMPTS = 3
HISTORY_LOOKBACK_DAYS = 14  # look back this many days for R007 enforcement

# Compute the target week (upcoming Monday) at module load
def next_monday():
    today = date.today()
    days_ahead = 0 if today.weekday() == 0 else (7 - today.weekday())
    return today + timedelta(days=days_ahead)

WEEK_START = next_monday()
DISH_LOOKUP = {}


# =========================================================
# Data loading
# =========================================================

def load_all_data():
    sheet = get_sheet()
    dishes = load_tab(sheet, "dishes")
    family = load_tab(sheet, "family")
    rules = load_tab(sheet, "rules")
    feedback = load_tab(sheet, "feedback")
    history = load_tab(sheet, "menu_history")
    active_rules = [r for r in rules if str(r.get("active", "")).strip().lower() == "yes"]
    return dishes, family, active_rules, feedback, history


def summarize_recent_feedback(feedback, days=14):
    """Summarize dish ratings over the last N days."""
    if not feedback:
        return [], [], []

    cutoff = (date.today() - timedelta(days=days)).isoformat()
    dish_ratings = {}
    for row in feedback:
        try:
            row_date = str(row.get("date", ""))
            if row_date < cutoff:
                continue
            dish_id = row.get("dish_id", "")
            rating = int(row.get("rating", 0))
            if not dish_id or rating < 1 or rating > 5:
                continue
            dish_ratings.setdefault(dish_id, []).append(rating)
        except (ValueError, TypeError):
            continue

    favorites, avoid, blacklist = [], [], []
    for dish_id, ratings in dish_ratings.items():
        avg = sum(ratings) / len(ratings)
        if 1 in ratings:
            blacklist.append(dish_id)
        elif avg >= 4:
            favorites.append(dish_id)
        elif avg <= 2:
            avoid.append(dish_id)
    return favorites, avoid, blacklist


def summarize_recent_history(history, days=HISTORY_LOOKBACK_DAYS, dish_lookup=None):
    """
    Build a set of dish_ids cooked in the last N days,
    plus a formatted 'do not repeat' list for the prompt.

    Returns (recent_dish_ids_set, formatted_prompt_text).
    """
    if not history:
        return set(), "(no history yet — first week)"

    cutoff = (date.today() - timedelta(days=days)).isoformat()

    recent = {}  # dish_id -> most recent date it appeared
    for row in history:
        try:
            row_date = str(row.get("date", ""))
            if row_date < cutoff:
                continue
            did = str(row.get("dish_id", "")).strip()
            if not did:
                continue
            # Keep the most recent date per dish
            if did not in recent or row_date > recent[did]:
                recent[did] = row_date
        except (ValueError, TypeError):
            continue

    if not recent:
        return set(), "(no dishes in recent history)"

    lines = []
    for did in sorted(recent.keys()):
        name = (dish_lookup or {}).get(did, "?")
        cooked_on = recent[did]
        lines.append(f"  - {did} ({name}) — last cooked {cooked_on}")

    return set(recent.keys()), "\n".join(lines)


# =========================================================
# Prompt building
# =========================================================

def build_week_prompt(dishes, family, rules, favorites, avoid, blacklist, recent_history_text):
    fav_str = ", ".join(favorites) if favorites else "(none yet)"
    avoid_str = ", ".join(avoid) if avoid else "(none)"
    blacklist_str = ", ".join(blacklist) if blacklist else "(none)"

    feedback_block = f"""
==================== RECENT FEEDBACK (last 14 days) ====================
Family loves these dishes (rated 4-5 recently) — FAVOR them in the plan:
{fav_str}

Family dislikes these dishes (avg rating ≤2) — AVOID unless necessary:
{avoid_str}

Family truly hates these (rated 1 at least once) — DO NOT USE:
{blacklist_str}
"""

    history_block = f"""
==================== RECENT COOKING HISTORY (last {HISTORY_LOOKBACK_DAYS} days) ====================
These dishes were already cooked recently. DO NOT repeat any of them
in the new plan (R007: no repeats in 10-day window):

{recent_history_text}
"""

    return f"""You are a thoughtful family meal planner for the Shetty family in Bengaluru, India.
{feedback_block}
{history_block}

==================== FAMILY ====================
{json.dumps(family, indent=2)}

==================== ACTIVE RULES ====================
{json.dumps([{
  'rule_id': r['rule_id'],
  'category': r.get('rule_category', ''),
  'description': r['rule_description']
} for r in rules], indent=2)}

==================== DISH CATALOG ====================
{json.dumps(dishes, indent=2)}

==================== TASK ====================
Generate a 7-day menu plan for the week starting {WEEK_START.isoformat()} (Monday).

For each day, output:
- day_of_week, date (ISO), is_training_day (bool — Tue/Thu/Sat = training days)
- breakfast: {{"dish_id", "dish_name", "supplement" (optional), "reasoning"}}
- lunch: {{"dish_id", "dish_name", "reasoning"}}
- dinner: {{"dish_id", "dish_name", "protein_booster_dish_id" (optional), "reasoning"}}
- fruit_of_the_day: string

Rules to apply carefully:
1. R007: Do NOT use any dish_id from the "recent cooking history" list above.
2. R011: Default dinner = lunch leftovers (dinner.dish_id == lunch.dish_id).
3. R010: Training days (Tue/Thu/Sat) need a high-protein booster in dinner.
4. Complexity budget: weekday breakfasts should be complexity 1.
5. Variety across the 7 days — no dish (except leftovers pattern) repeats within the plan.

Return STRICT JSON:
{{
  "week_starting": "{WEEK_START.isoformat()}",
  "days": [
    {{
      "day_of_week": "Monday",
      "date": "...",
      "is_training_day": false,
      "breakfast": {{...}},
      "lunch": {{...}},
      "dinner": {{...}},
      "fruit_of_the_day": "Banana"
    }},
    ... 7 days total ...
  ],
  "rules_applied": ["R001", "R007", "R011", ...],
  "notes": "one or two sentences on tradeoffs"
}}

Output ONLY the JSON. No markdown. No commentary.
"""


def build_retry_prompt(base_prompt, violations, previous_plan_json):
    return f"""{base_prompt}

==================== YOUR PREVIOUS ATTEMPT ====================
{previous_plan_json}

==================== VIOLATIONS TO FIX ====================
{chr(10).join(f'- {v}' for v in violations)}

Regenerate the plan, fixing these specific violations.
Output ONLY the corrected JSON.
"""


# =========================================================
# Validation
# =========================================================

def validate_plan(plan, dishes, recent_dish_ids):
    """
    Return list of human-readable violations.
    Empty list means all checks passed.
    """
    violations = []
    dish_ids = {d.get("dish_id"): d for d in dishes}

    if "days" not in plan or len(plan["days"]) != 7:
        violations.append("Plan must have exactly 7 days")
        return violations

    # Track intra-plan dish usage per slot
    breakfasts = []
    lunches = []
    for day in plan["days"]:
        for slot in ("breakfast", "lunch", "dinner"):
            slot_data = day.get(slot, {})
            did = slot_data.get("dish_id")
            if did and did not in dish_ids:
                violations.append(
                    f"{day.get('day_of_week','?')} {slot}: dish_id {did} not in catalog"
                )
        bf_id = day.get("breakfast", {}).get("dish_id")
        l_id = day.get("lunch", {}).get("dish_id")
        if bf_id: breakfasts.append(bf_id)
        if l_id: lunches.append(l_id)

    # R007: No repeat with recent history
    for day in plan["days"]:
        for slot in ("breakfast", "lunch"):
            did = day.get(slot, {}).get("dish_id")
            if did and did in recent_dish_ids:
                violations.append(
                    f"R007 violation: {day.get('day_of_week','?')} {slot} "
                    f"= {did} was cooked in last {HISTORY_LOOKBACK_DAYS} days"
                )
        # Booster too — but with softer wording since catalog is limited
        booster = day.get("dinner", {}).get("protein_booster_dish_id")
        if booster and booster in recent_dish_ids:
            # Soft warning, not hard fail (booster catalog is small)
            print(f"   ⚠️  Soft: {day.get('day_of_week','?')} booster {booster} was recent")

    # No repeat within the plan (breakfasts unique, lunches unique)
    if len(set(breakfasts)) != len(breakfasts):
        dups = [b for b in breakfasts if breakfasts.count(b) > 1]
        violations.append(f"Breakfast repeated within week: {set(dups)}")
    if len(set(lunches)) != len(lunches):
        dups = [l for l in lunches if lunches.count(l) > 1]
        violations.append(f"Lunch repeated within week: {set(dups)}")

    return violations


# =========================================================
# Gemini call with retry
# =========================================================

def call_gemini_with_retry(client, prompt):
    for attempt in range(4):
        try:
            response = client.models.generate_content(
                model=MODEL_NAME,
                contents=prompt,
            )
            return response
        except ServerError:
            wait = 5 * (2 ** attempt)
            print(f"   ⏳ Gemini busy. Waiting {wait}s...")
            time.sleep(wait)
        except ClientError as e:
            if "429" in str(e):
                print(f"   ⏳ Rate-limited. Waiting 60s...")
                time.sleep(60)
            else:
                raise
    raise RuntimeError("Gemini unavailable after retries.")


# =========================================================
# Pretty print
# =========================================================

def pretty_print_week(plan):
    print(f"\n📆 Week starting {plan.get('week_starting','?')}\n")
    for day in plan.get("days", []):
        tag = " 🏃‍♀️" if day.get("is_training_day") else ""
        print(f"{day.get('day_of_week','?')} ({day.get('date','?')}){tag}")
        print(f"  BF: {day['breakfast'].get('dish_name','?')}")
        print(f"  L:  {day['lunch'].get('dish_name','?')}")
        print(f"  D:  {day['dinner'].get('dish_name','?')} (leftovers)")
        booster = day['dinner'].get('protein_booster_dish_id')
        if booster:
            print(f"      💪 booster: {booster}")
        print(f"  🍎 {day.get('fruit_of_the_day','?')}\n")


# =========================================================
# Main
# =========================================================

def main():
    # Immutability + force
    force = "--force" in sys.argv
    target_week = WEEK_START.isoformat()
    plan_path = "latest_week_plan.json"

    if not force and os.path.exists(plan_path):
        try:
            with open(plan_path, "r", encoding="utf-8") as f:
                existing = json.load(f)
            if existing.get("week_starting", "") == target_week:
                print(f"⚠️  Plan for week {target_week} already exists.")
                print(f"   Refusing to regenerate. Use --force to override.")
                return
        except (json.JSONDecodeError, KeyError):
            pass

    print(f"📋 Planning week starting {WEEK_START.isoformat()} ({WEEK_START.strftime('%A')})\n")

    print("Loading data from Google Sheets...")
    dishes, family, rules, feedback, history = load_all_data()
    favorites, avoid, blacklist = summarize_recent_feedback(feedback, days=14)
    DISH_LOOKUP.update({d["dish_id"]: d["dish_name"] for d in dishes})
    recent_dish_ids, recent_text = summarize_recent_history(
        history, days=HISTORY_LOOKBACK_DAYS, dish_lookup=DISH_LOOKUP
    )
    print(f"  - Feedback signals: {len(favorites)} favorites, {len(avoid)} avoid, {len(blacklist)} blacklist")
    print(f"  - History: {len(recent_dish_ids)} dishes cooked in last {HISTORY_LOOKBACK_DAYS} days")
    print(f"  - {len(dishes)} dishes, {len(family)} family members, {len(rules)} active rules\n")

    print("Building prompt and calling Gemini (takes ~10-20s for a full week)...")
    prompt = build_week_prompt(dishes, family, rules, favorites, avoid, blacklist, recent_text)

    client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
    plan = None
    violations = []

    for attempt in range(1, MAX_VALIDATION_ATTEMPTS + 1):
        print(f"\n--- Attempt {attempt}/{MAX_VALIDATION_ATTEMPTS} ---")

        current_prompt = prompt if attempt == 1 else build_retry_prompt(
            prompt, violations, json.dumps(plan, indent=2)
        )

        response = call_gemini_with_retry(client, current_prompt)
        raw = response.text.strip()
        if raw.startswith("```"):
            raw = raw.strip("`").lstrip("json").strip()

        try:
            plan = json.loads(raw)
        except json.JSONDecodeError:
            print(f"⚠️  Attempt {attempt}: could not parse JSON. Raw:\n{raw}\n")
            if attempt == MAX_VALIDATION_ATTEMPTS:
                raise
            continue

        violations = validate_plan(plan, dishes, recent_dish_ids)
        if not violations:
            print(f"✅ Plan passed validation on attempt {attempt}")
            break
        else:
            print(f"⚠️  Found {len(violations)} violation(s):")
            for v in violations:
                print(f"   - {v}")
            if attempt == MAX_VALIDATION_ATTEMPTS:
                print("⚠️  Max attempts reached. Showing best attempt with remaining issues.")

    pretty_print_week(plan)

    with open("latest_week_plan.json", "w", encoding="utf-8") as f:
        json.dump(plan, f, indent=2)
    print(f"\n💾 Full plan saved to latest_week_plan.json")


if __name__ == "__main__":
    main()