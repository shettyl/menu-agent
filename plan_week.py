"""
Day 4: Generate a full 7-day menu plan with agentic validation loop.

Architecture:
1. Load dishes, family, rules from Google Sheets
2. Build prompt and ask Gemini for a week plan
3. Validate the plan in Python against hard rules
4. If violations found, send feedback to Gemini and retry (up to 3 attempts)
5. Print final plan and save to disk

Resilient to transient API errors (503/429) with exponential backoff.
"""

import os
import json
import time
from datetime import date, timedelta
from dotenv import load_dotenv
from google import genai
from google.genai.errors import ServerError, ClientError
from load_data import get_sheet, load_tab

load_dotenv()

# ---------- Inputs you can tweak ----------
def next_monday():
    """Returns the date of the upcoming Monday (never today, always next)."""
    today = date.today()
    days_ahead = (7 - today.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7
    return today + timedelta(days=days_ahead)


WEEK_START = next_monday()
DAYS = [(WEEK_START + timedelta(days=i)) for i in range(7)]
TRAINING_DAYS = {"Tuesday", "Thursday", "Saturday"}
MAX_VALIDATION_ATTEMPTS = 3
MODEL_NAME = "gemini-2.5-flash"
# ------------------------------------------

# Populated in main(); used by pretty_print_week to resolve dish_id -> name
DISH_LOOKUP = {}


def load_all_data():
    sheet = get_sheet()
    dishes = load_tab(sheet, "dishes")
    family = load_tab(sheet, "family")
    rules = load_tab(sheet, "rules")
    feedback = load_tab(sheet, "feedback")
    active_rules = [r for r in rules if str(r.get("active", "")).strip().lower() == "yes"]
    return dishes, family, active_rules, feedback


def summarize_recent_feedback(feedback, days=14):
    """Summarize dish ratings over the last N days. Returns (favorites, avoid, blacklist)."""
    if not feedback:
        return [], [], []

    cutoff = (date.today() - timedelta(days=days)).isoformat()

    # Collect ratings per dish_id
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

    favorites = []
    avoid = []
    blacklist = []
    for dish_id, ratings in dish_ratings.items():
        avg = sum(ratings) / len(ratings)
        if 1 in ratings:
            blacklist.append(dish_id)
        elif avg >= 4:
            favorites.append(dish_id)
        elif avg <= 2:
            avoid.append(dish_id)

    return favorites, avoid, blacklist


def format_dishes_for_prompt(dishes):
    lines = []
    for d in dishes:
        line = (
            f"[{d.get('dish_id')}] {d.get('dish_name')} "
            f"| slot={d.get('meal_slot')} "
            f"| diet={d.get('diet_type')} "
            f"| protein={d.get('protein_level')} "
            f"| complexity={d.get('prep_complexity')} "
            f"| cook_can_make={d.get('cook_can_make')}"
        )
        notes = d.get("notes")
        if notes:
            line += f" | notes={notes}"
        lines.append(line)
    return "\n".join(lines)


def format_family_for_prompt(family):
    return "\n".join(
        f"- {f.get('person_name')} ({f.get('age')}y): "
        f"diet={f.get('diet')}, activity={f.get('activity_level')}, "
        f"protein_need={f.get('protein_need')}, "
        f"dislikes={f.get('dislikes') or 'none'}, "
        f"notes={f.get('notes') or 'none'}"
        for f in family
    )


def format_rules_for_prompt(rules):
    return "\n".join(
        f"[{r.get('rule_id')}] ({r.get('rule_category')}) {r.get('rule_description')}"
        for r in rules
    )


def format_week_calendar():
    lines = []
    for d in DAYS:
        day_name = d.strftime("%A")
        is_training = day_name in TRAINING_DAYS
        is_weekend = day_name in ("Saturday", "Sunday")
        flags = []
        if is_training:
            flags.append("TRAINING DAY for Shloka")
        if is_weekend:
            flags.append("WEEKEND - one complex dish allowed")
        flag_str = f" [{', '.join(flags)}]" if flags else ""
        lines.append(f"- {d.isoformat()} ({day_name}){flag_str}")
    return "\n".join(lines)


def build_week_prompt(dishes, family, rules, favorites, avoid, blacklist):
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

    return f"""You are a thoughtful family meal planner for the Shetty family in Bengaluru, India.
{feedback_block}
==================== FAMILY ====================
{format_family_for_prompt(family)}

==================== AVAILABLE DISHES ====================
Pick ONLY from these dishes (use dish_id to reference). Do not invent new dishes.

{format_dishes_for_prompt(dishes)}

==================== OPERATING RULES ====================
{format_rules_for_prompt(rules)}

==================== WEEK CALENDAR ====================
Plan menus for these 7 days:

{format_week_calendar()}

==================== TASK ====================
Generate a 7-day menu plan covering breakfast, lunch, and dinner for each day.

CRITICAL CONSTRAINTS (validated automatically — violations will be rejected):

C1. UNIQUENESS: No dish_id may appear more than once across the 7 breakfasts and
    7 lunches. Dinner may legitimately reuse the same dish_id as that day's lunch
    (representing "lunch leftovers eaten for dinner"). All breakfast and lunch
    dish_ids across the week must be distinct.
    Example violation: D001 used on Tuesday breakfast AND Friday breakfast — REJECT.

C2. CONCENTRATED PROTEIN VARIETY WITHIN A DAY: Across the 3 meals of any single day,
    no CONCENTRATED protein source may appear more than once. Concentrated proteins are:
    - EGG: dishes with diet_type "egg" or "egg" in dish_name
    - PANEER: dishes with paneer in name or ingredients
    - CHICKEN: dishes with chicken
    - LEGUME: channa, sprouts, horsegram, rajma
    (Dal is NOT a concentrated protein — Indian families eat dal at multiple meals
    and idli/dosa contain urad dal structurally. Dal repetition is fine.)
    Example violation: Neer Dosa with Egg Curry (breakfast) + Egg Curry Jeera Rice (lunch)
    — both EGG protein — REJECT.

C3. TRAINING DAY PROTEIN: On Tuesday, Thursday, Saturday, the dinner OR the
    protein_booster_dish_id must be high-protein (paneer/chicken/legume based).
    If dinner is "lunch leftovers", a high-protein booster is mandatory.

C4. WEEKDAY BREAKFAST COMPLEXITY: On Monday–Friday, breakfast dish_id must have
    complexity=1 (cook's daily routine). NO complexity=2 dishes for weekday breakfasts.
    Weekday lunch or dinner may include ONE complexity=2 dish per day.
    Complexity=3 dishes: only on Saturday or Sunday.

C5. CUISINE VARIETY: Use at least 3 distinct cuisines across the 7 days.

C6. DINNER PATTERN: By default dinner reuses the same dish_id as lunch (lunch leftovers).
    Always also suggest a "protein_booster_dish_id" — a high-protein salad/bowl from the catalog.
    On training days, the protein_booster must be a substantial high-protein main.

THINK STEP BY STEP BEFORE GENERATING THE JSON:

Step 1: Map out concentrated proteins in your dish catalog by category:
  - EGG breakfasts: list dish_ids whose name or diet contains "egg"
  - EGG lunches: list dish_ids whose name or diet contains "egg"
  - PANEER breakfasts: list dish_ids with "paneer" in name
  - PANEER lunches: list dish_ids with "paneer" in name
  - CHICKEN options: list chicken dish_ids
  - LEGUME options: list dish_ids with channa/sprouts/horsegram/rajma

Step 2: For each day, decide the concentrated protein source for breakfast AND lunch.
  THEY MUST BE DIFFERENT. If breakfast is EGG, lunch cannot be EGG.
  Pick from different categories per day.

Step 3: For training days (Tue/Thu/Sat), pick a HIGH-PROTEIN dinner or booster
  (paneer/chicken/legume). It must NOT match the day's other protein sources.

Step 4: For weekday breakfasts (Mon-Fri), confirm complexity=1 in the catalog.
  If you picked a complexity=2 breakfast, replace it.

Step 5: Check no breakfast dish_id is used twice across the 7 days.
Step 6: Check no lunch dish_id is used twice across the 7 days.

After completing these 6 mental steps, output ONLY the JSON. Do not show the steps.

{{
  "week_starting": "{WEEK_START.isoformat()}",
  "days": [
    {{
      "date": "YYYY-MM-DD",
      "day_of_week": "Monday",
      "is_training_day": false,
      "breakfast": {{ "dish_id": "Dxxx", "dish_name": "...", "supplement": "boiled eggs/fruit/none", "reasoning": "..." }},
      "lunch":     {{ "dish_id": "Dxxx", "dish_name": "...", "reasoning": "..." }},
      "dinner":    {{ "dish_id": "Dxxx", "dish_name": "...", "protein_booster_dish_id": "Dxxx or null", "reasoning": "..." }},
      "fruit_of_the_day": "Apple/Banana/etc"
    }}
  ],
  "week_summary": {{
    "cuisines_used": ["South Indian", "North Indian", "Continental"],
    "unique_dishes_used": 14,
    "rules_actively_applied": ["R001", "R003"],
    "potential_issues": "Any tradeoffs or things to watch for"
  }}
}}

NOTES ON OUTPUT:
- "fruit_of_the_day" must be just the fruit name (e.g., "Apple", "Banana"), NOT a dish_id.
  Pick from available fruits in the catalog but output only the plain fruit name.
- "dish_name" fields should be just the dish name from the catalog, no dish_id prefix.
Output ONLY the JSON. No markdown fences. No commentary before or after.
"""


def validate_plan(plan, dishes):
    """
    Walk the generated plan and check every hard rule.
    Returns a list of violation strings. Empty list = clean plan.
    """
    violations = []
    dish_lookup = {d["dish_id"]: d for d in dishes}

    # --- C1: dish_id uniqueness for breakfast + lunch ---
    # Dinner is allowed to repeat lunch (leftovers).
    bf_lunch_used = []
    for day in plan["days"]:
        for slot in ("breakfast", "lunch"):
            did = day[slot].get("dish_id")
            if did:
                bf_lunch_used.append((day["day_of_week"], slot, did))

    seen = {}
    for day_name, slot, did in bf_lunch_used:
        if did in seen:
            prev_day, prev_slot = seen[did]
            violations.append(
                f"C1 (uniqueness): {did} appears in {prev_day} {prev_slot} AND {day_name} {slot}"
            )
        else:
            seen[did] = (day_name, slot)

    # --- C2: concentrated protein within a day ---
    def classify_protein(dish):
        """
        Classify a dish's primary CONCENTRATED protein source.
        DAL is intentionally excluded — dal repetition across meals is normal.
        Only flag concentrated proteins: eggs, paneer, chicken, legumes.
        """
        if not dish:
            return None
        name = (dish.get("dish_name") or "").lower()
        diet = (dish.get("diet_type") or "").lower()
        ingredients = (dish.get("main_ingredients") or "").lower()
        if diet == "egg" or "egg" in name:
            return "EGG"
        if "paneer" in name or "paneer" in ingredients:
            return "PANEER"
        if "chicken" in name or "chicken" in ingredients:
            return "CHICKEN"
        if any(w in ingredients for w in ["channa", "sprouts", "horsegram", "rajma"]):
            return "LEGUME"
        return None

    for day in plan["days"]:
        lunch_id = day["lunch"].get("dish_id")
        proteins_today = []
        for slot in ("breakfast", "lunch", "dinner"):
            did = day[slot].get("dish_id")
            # Skip dinner if it's just leftovers from lunch
            if slot == "dinner" and did == lunch_id:
                continue
            dish = dish_lookup.get(did, {})
            p = classify_protein(dish)
            if p:
                proteins_today.append((slot, p, did))

        seen_p = {}
        for slot, p, did in proteins_today:
            if p in seen_p:
                prev_slot, prev_did = seen_p[p]
                violations.append(
                    f"C2 ({day['day_of_week']}): protein '{p}' repeats in "
                    f"{prev_slot} ({prev_did}) and {slot} ({did})"
                )
            else:
                seen_p[p] = (slot, did)

    # --- C4: weekday breakfast must be complexity=1 ---
    weekday_names = {"Monday", "Tuesday", "Wednesday", "Thursday", "Friday"}
    for day in plan["days"]:
        if day["day_of_week"] not in weekday_names:
            continue
        bf_id = day["breakfast"].get("dish_id")
        bf_dish = dish_lookup.get(bf_id, {})
        try:
            bf_complexity = int(bf_dish.get("prep_complexity", 1))
        except (ValueError, TypeError):
            bf_complexity = 1
        if bf_complexity > 1:
            violations.append(
                f"C4 ({day['day_of_week']}): weekday breakfast {bf_id} "
                f"({bf_dish.get('dish_name', '')}) has complexity={bf_complexity}, must be 1"
            )

    return violations


def build_retry_prompt(original_prompt, violations, previous_plan_json):
    """Compose a follow-up prompt that tells Gemini what it got wrong."""
    return f"""{original_prompt}

==================== YOUR PREVIOUS ATTEMPT ====================
{previous_plan_json}

==================== VALIDATION FAILURES ====================
A strict validator checked your previous plan and found these RULE VIOLATIONS:

{chr(10).join(f"- {v}" for v in violations)}

Generate a NEW plan that fixes EVERY violation listed above while keeping
all the parts of the previous plan that were correct. Output ONLY the JSON.
"""


def call_gemini_with_retry(client, prompt):
    """Call Gemini with exponential backoff on transient errors (503/429)."""
    for net_attempt in range(4):
        try:
            response = client.models.generate_content(
                model=MODEL_NAME,
                contents=prompt,
            )
            return response
        except ServerError:
            wait = 5 * (2 ** net_attempt)
            print(f"   ⏳ Gemini busy (503). Waiting {wait}s before retry...")
            time.sleep(wait)
        except ClientError as e:
            if "429" in str(e):
                print(f"   ⏳ Gemini rate-limited (429). Waiting 30s...")
                time.sleep(30)
            else:
                raise
    raise RuntimeError("Gemini unavailable after 4 retries. Try again later.")


def safe_get_name(slot_dict, default_label="—"):
    """Resolve dish_name with fallbacks to avoid KeyError."""
    did = slot_dict.get("dish_id", "")
    return slot_dict.get("dish_name") or DISH_LOOKUP.get(did, did or default_label)


def pretty_print_week(plan):
    print(f"\n📅 WEEK STARTING {plan['week_starting']}")
    print("=" * 70)
    for day in plan["days"]:
        training_tag = "  🏃 TRAINING" if day.get("is_training_day") else ""
        print(f"\n{day['day_of_week']:<10} {day['date']}{training_tag}")

        bf = day["breakfast"]
        bf_name = safe_get_name(bf)
        supp = (bf.get("supplement") or "").strip().lower()
        if supp and supp not in ("none", "null", ""):
            print(f"  🍳 Breakfast: {bf_name}  +  {supp}")
        else:
            print(f"  🍳 Breakfast: {bf_name}")

        print(f"  🍛 Lunch:     {safe_get_name(day['lunch'])}")
        print(f"  🍽️  Dinner:    {safe_get_name(day['dinner'])}")

        booster_id = day["dinner"].get("protein_booster_dish_id")
        if booster_id and str(booster_id).lower() not in ("null", "none", ""):
            booster_name = DISH_LOOKUP.get(booster_id, booster_id)
            print(f"     + Protein booster:  {booster_name}  ({booster_id})")

        print(f"  🍎 Fruit:     {day.get('fruit_of_the_day', '-')}")

    summary = plan.get("week_summary", {})
    print("\n" + "=" * 70)
    print("📊 WEEK SUMMARY")
    print("=" * 70)
    print(f"Cuisines used:        {', '.join(summary.get('cuisines_used', []))}")
    print(f"Unique dishes:        {summary.get('unique_dishes_used', '-')}")
    print(f"Rules applied:        {', '.join(summary.get('rules_actively_applied', []))}")
    if summary.get("potential_issues"):
        print(f"Notes:                {summary['potential_issues']}")


def main():
    print(f"📋 Planning week starting {WEEK_START.isoformat()} ({WEEK_START.strftime('%A')})\n")

    print("Loading data from Google Sheets...")
    dishes, family, rules, feedback = load_all_data()
    favorites, avoid, blacklist = summarize_recent_feedback(feedback, days=14)
    print(f"  - Feedback signals: {len(favorites)} favorites, {len(avoid)} avoid, {len(blacklist)} blacklist")
    DISH_LOOKUP.update({d["dish_id"]: d["dish_name"] for d in dishes})
    print(f"  - {len(dishes)} dishes, {len(family)} family members, {len(rules)} active rules\n")

    print("Building prompt and calling Gemini (takes ~10-20s for a full week)...")
    prompt = build_week_prompt(dishes, family, rules, favorites, avoid, blacklist)

    client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
    plan = None
    violations = []

    for attempt in range(1, MAX_VALIDATION_ATTEMPTS + 1):
        print(f"\n--- Attempt {attempt}/{MAX_VALIDATION_ATTEMPTS} ---")

        if attempt == 1:
            current_prompt = prompt
        else:
            current_prompt = build_retry_prompt(
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

        violations = validate_plan(plan, dishes)
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