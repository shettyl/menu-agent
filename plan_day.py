"""
Day 3 Step 9: Generate a single day's menu using:
- Real dish catalog (Google Sheets)
- Real family profiles
- Real operating rules
- Gemini as the planner

Designed for fast iteration. Edit and re-run.
"""

import os
import json
from datetime import date
from dotenv import load_dotenv
from google import genai
from load_data import get_sheet, load_tab

load_dotenv()

# ---------- Inputs you can tweak per run ----------
TARGET_DATE = date.today().isoformat()  # YYYY-MM-DD; change to test specific days
DAY_OF_WEEK = date.today().strftime("%A")  # Monday, Tuesday, etc.
IS_TRAINING_DAY = DAY_OF_WEEK in ("Tuesday", "Thursday", "Saturday")
# --------------------------------------------------


def load_all_data():
    """Pull dishes, family, and active rules from the sheet."""
    sheet = get_sheet()
    dishes = load_tab(sheet, "dishes")
    family = load_tab(sheet, "family")
    rules = load_tab(sheet, "rules")
    # Only keep rules where active == "yes"
    active_rules = [r for r in rules if str(r.get("active", "")).strip().lower() == "yes"]
    return dishes, family, active_rules


def format_dishes_for_prompt(dishes):
    """Compress dish records into a compact format Gemini can parse easily."""
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
    lines = []
    for f in family:
        lines.append(
            f"- {f.get('person_name')} ({f.get('age')}y): "
            f"diet={f.get('diet')}, activity={f.get('activity_level')}, "
            f"protein_need={f.get('protein_need')}, "
            f"dislikes={f.get('dislikes') or 'none'}, "
            f"notes={f.get('notes') or 'none'}"
        )
    return "\n".join(lines)


def format_rules_for_prompt(rules):
    lines = []
    for r in rules:
        lines.append(f"[{r.get('rule_id')}] ({r.get('rule_category')}) {r.get('rule_description')}")
    return "\n".join(lines)


def build_prompt(dishes, family, rules):
    return f"""You are a thoughtful family meal planner for the Shetty family in Bengaluru, India.

==================== FAMILY ====================
{format_family_for_prompt(family)}

==================== AVAILABLE DISHES ====================
Pick ONLY from these dishes (use dish_id to reference). Do not invent new dishes.

{format_dishes_for_prompt(dishes)}

==================== OPERATING RULES ====================
{format_rules_for_prompt(rules)}

==================== TODAY'S CONTEXT ====================
- Date: {TARGET_DATE}
- Day: {DAY_OF_WEEK}
- Training day for Shloka: {"YES (higher protein needed)" if IS_TRAINING_DAY else "no"}

==================== YOUR TASK ====================
Generate a menu plan for {DAY_OF_WEEK} only. Return STRICT JSON in this exact format:

{{
  "date": "{TARGET_DATE}",
  "day_of_week": "{DAY_OF_WEEK}",
  "breakfast": {{
    "dish_id": "Dxxx",
    "dish_name": "...",
    "supplement": "boiled eggs / none / fruit",
    "reasoning": "one sentence why this fits today"
  }},
  "lunch": {{
    "dish_id": "Dxxx",
    "dish_name": "...",
    "reasoning": "one sentence"
  }},
  "dinner": {{
    "dish_id": "Dxxx",
    "dish_name": "...",
    "reasoning": "one sentence — and note who eats what if it differs (e.g. Anitha salad bowl, Lokesh+Shloka leftovers)"
  }},
  "fruit_of_the_day": "Apple / Banana / etc",
  "rules_applied": ["R001", "R003", ...],
  "protein_summary": {{
    "Lokesh": "approximate protein level across the day: high/medium/low",
    "Anitha": "...",
    "Shloka": "..."
  }}
}}

Rules for output:
1. Output ONLY the JSON, no other text, no markdown code fences.
2. Use real dish_ids from the catalog above.
3. Apply the operating rules — list which ones you actively used in "rules_applied".
4. If it's a training day for Shloka, ensure her dinner is high-protein.
"""


def main():
    print(f"Planning menu for {DAY_OF_WEEK}, {TARGET_DATE}")
    print(f"Training day for Shloka: {IS_TRAINING_DAY}\n")

    print("Loading data from Google Sheets...")
    dishes, family, rules = load_all_data()
    print(f"  - {len(dishes)} dishes, {len(family)} family members, {len(rules)} active rules\n")

    print("Building prompt and calling Gemini...")
    prompt = build_prompt(dishes, family, rules)

    client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
    )

    raw = response.text.strip()
    # Strip code fences if Gemini sneaks them in despite our instruction
    if raw.startswith("```"):
        raw = raw.strip("`").lstrip("json").strip()

    print("=" * 70)
    print("RAW GEMINI OUTPUT")
    print("=" * 70)
    print(raw)
    print("=" * 70)

    # Try to parse as JSON
    try:
        plan = json.loads(raw)
        print("\n✅ Parsed JSON successfully")
        print("\n--- HUMAN-READABLE SUMMARY ---")
        print(f"Date: {plan['date']} ({plan['day_of_week']})")
        print(f"Breakfast: {plan['breakfast']['dish_name']} "
              f"+ {plan['breakfast'].get('supplement', 'none')}")
        print(f"  reason: {plan['breakfast']['reasoning']}")
        print(f"Lunch:     {plan['lunch']['dish_name']}")
        print(f"  reason: {plan['lunch']['reasoning']}")
        print(f"Dinner:    {plan['dinner']['dish_name']}")
        print(f"  reason: {plan['dinner']['reasoning']}")
        print(f"Fruit:     {plan.get('fruit_of_the_day', 'n/a')}")
        print(f"Rules applied: {', '.join(plan.get('rules_applied', []))}")
        print("\nProtein summary:")
        for person, level in plan.get("protein_summary", {}).items():
            print(f"  - {person}: {level}")
    except json.JSONDecodeError as e:
        print(f"\n⚠️  Could not parse JSON: {e}")
        print("Output may need prompt refinement.")


if __name__ == "__main__":
    main()