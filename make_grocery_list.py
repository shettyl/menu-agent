"""
Day 4 Step 2: Generate a grocery list from the latest week plan.

Reads latest_week_plan.json + dish catalog, asks Gemini to:
- Aggregate ingredients across all dishes
- Estimate realistic household quantities for a family of 3
- Group by buy-day (Sunday/Wednesday/Friday)
"""

import os
import json
import time
from dotenv import load_dotenv
from google import genai
from google.genai.errors import ServerError, ClientError
from load_data import get_sheet, load_tab

load_dotenv()

MODEL_NAME = "gemini-2.5-flash-lite"
PLAN_FILE = "latest_week_plan.json"


def load_plan():
    with open(PLAN_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def load_dish_catalog():
    sheet = get_sheet()
    return load_tab(sheet, "dishes")


def gather_dishes_in_plan(plan, dish_lookup):
    """
    Pull every dish from the week (breakfast, lunch, dinner, booster) and
    return their full catalog rows. De-duplicated by dish_id.
    """
    used_ids = set()
    for day in plan["days"]:
        for slot in ("breakfast", "lunch", "dinner"):
            did = day[slot].get("dish_id")
            if did:
                used_ids.add(did)
        booster = day["dinner"].get("protein_booster_dish_id")
        if booster and str(booster).lower() not in ("null", "none", ""):
            used_ids.add(booster)
    return [dish_lookup[did] for did in used_ids if did in dish_lookup]


def format_dishes_for_grocery(dishes_used, plan):
    """Compact view: each dish with how often it's used in the week."""
    usage = {}
    for day in plan["days"]:
        for slot in ("breakfast", "lunch", "dinner"):
            did = day[slot].get("dish_id")
            if did:
                usage[did] = usage.get(did, 0) + 1
        booster = day["dinner"].get("protein_booster_dish_id")
        if booster and str(booster).lower() not in ("null", "none", ""):
            usage[booster] = usage.get(booster, 0) + 1

    lines = []
    for d in dishes_used:
        did = d.get("dish_id")
        count = usage.get(did, 1)
        ingredients = d.get("main_ingredients", "")
        perishable = d.get("key_ingredients_perishable", "")
        meat_fish = d.get("key_ingredients_meat_fish", "")
        line = f"[{did}] {d.get('dish_name')} (used {count}x this week)"
        if ingredients:
            line += f"\n   main: {ingredients}"
        if perishable:
            line += f"\n   perishable: {perishable}"
        if meat_fish:
            line += f"\n   meat/fish: {meat_fish}"
        lines.append(line)
    return "\n".join(lines)


def build_grocery_prompt(plan, dishes_used):
    week_dishes = format_dishes_for_grocery(dishes_used, plan)
    return f"""You are a grocery list planner for the Shetty family in Bengaluru — 3 people
(Lokesh, Anitha 40s; Shloka 12, active sports player). Their cook prepares meals daily.

Below is the planned week of meals (dish_id, name, how often used) with each dish's ingredients.

==================== THIS WEEK'S MENU ====================
Week starting: {plan["week_starting"]}

Dishes to be cooked:
{week_dishes}

==================== TASK ====================
Generate a consolidated grocery list grouped by BUY-DAY:

- SUNDAY — Pantry & dry goods that last 1+ week:
    rice, all dals (toor/moong/urad/etc.), wheat flour, sooji, poha,
    spices, oil, ghee, peanuts, tamarind, jaggery, dry coconut, masalas

- WEDNESDAY — Fresh vegetables, herbs, leafy greens (last 2-3 days):
    all gourds, beans, leafy greens, tomato, onion, ginger, garlic,
    coriander, mint, curry leaves, capsicum, broccoli, zucchini, etc.

- FRIDAY — Perishables for the weekend:
    chicken, fish, eggs, paneer, fresh curd (extra), milk (extra)

For each item, estimate a SENSIBLE QUANTITY for a family of 3 cooking 21 meals.
Examples of realistic quantities:
- Tomato used in 6 dishes → ~1 kg
- Onion used in 8 dishes → ~1.5 kg
- Curry leaves used in 10 dishes → 1 small bunch (they go far)
- Eggs for 3 breakfasts + 2 lunches → ~2 dozen
- Chicken for 2 dinners → 1 kg

Be practical, not exhaustive. Skip household staples that any kitchen always has
(salt, mustard seeds, turmeric, basic spices), UNLESS the dish list shows unusual
spices used heavily (e.g., bisi bele masala for Saturday).

Return STRICT JSON:

{{
  "week_starting": "{plan["week_starting"]}",
  "sunday_pantry": [
    {{ "item": "Rice (raw, sona masuri)", "quantity": "2 kg", "for_dishes": ["D001", "D013"] }},
    {{ "item": "Toor dal", "quantity": "500 g", "for_dishes": ["D013"] }}
  ],
  "wednesday_fresh_veg": [
    {{ "item": "Onion", "quantity": "1.5 kg", "for_dishes": ["D002", "D019"] }},
    {{ "item": "Curry leaves", "quantity": "1 small bunch", "for_dishes": ["D001"] }}
  ],
  "friday_perishables": [
    {{ "item": "Eggs", "quantity": "2 dozen", "for_dishes": ["D002", "D020"] }},
    {{ "item": "Chicken (boneless)", "quantity": "1 kg", "for_dishes": ["D032"] }},
    {{ "item": "Paneer (fresh)", "quantity": "500 g", "for_dishes": ["D007", "D024"] }}
  ],
  "notes": "Any special items or substitutions worth flagging"
}}

Output ONLY the JSON. No markdown fences. No commentary.
"""


def call_gemini_with_retry(client, prompt):
    """Retry on transient 503/429."""
    for attempt in range(4):
        try:
            return client.models.generate_content(
                model=MODEL_NAME,
                contents=prompt,
            )
        except ServerError:
            wait = 5 * (2 ** attempt)
            print(f"   ⏳ Gemini busy. Waiting {wait}s...")
            time.sleep(wait)
        except ClientError as e:
            if "429" in str(e):
                print(f"   ⏳ Rate-limited. Waiting 30s...")
                time.sleep(30)
            else:
                raise
    raise RuntimeError("Gemini unavailable after retries.")


def pretty_print_grocery(grocery):
    print(f"\n🛒 GROCERY LIST — Week of {grocery['week_starting']}")
    print("=" * 70)

    sections = [
        ("SUNDAY",    "sunday_pantry",      "🥫 Pantry & dry goods"),
        ("WEDNESDAY", "wednesday_fresh_veg","🥦 Fresh veg & herbs"),
        ("FRIDAY",    "friday_perishables", "🍗 Meat, eggs, paneer"),
    ]

    for day, key, label in sections:
        items = grocery.get(key, [])
        print(f"\n📅 {day}  —  {label}")
        print("-" * 70)
        if not items:
            print("   (no items)")
            continue
        for item in items:
            qty = item.get("quantity", "?")
            name = item.get("item", "?")
            print(f"   • {name:<30}  {qty}")

    notes = grocery.get("notes", "")
    if notes:
        print(f"\n📝 Notes: {notes}")


def main():
    print("Loading week plan and dish catalog...")
    plan = load_plan()
    dishes = load_dish_catalog()
    dish_lookup = {d["dish_id"]: d for d in dishes}

    dishes_used = gather_dishes_in_plan(plan, dish_lookup)
    print(f"  - Plan has {len(dishes_used)} unique dishes\n")

    print("Building grocery prompt and calling Gemini...")
    prompt = build_grocery_prompt(plan, dishes_used)

    client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
    response = call_gemini_with_retry(client, prompt)

    raw = response.text.strip()
    if raw.startswith("```"):
        raw = raw.strip("`").lstrip("json").strip()

    try:
        grocery = json.loads(raw)
    except json.JSONDecodeError:
        print("\n⚠️  Could not parse JSON. Raw output:\n")
        print(raw)
        raise

    pretty_print_grocery(grocery)

    with open("latest_grocery_list.json", "w", encoding="utf-8") as f:
        json.dump(grocery, f, indent=2)
    print(f"\n💾 Grocery list saved to latest_grocery_list.json")


if __name__ == "__main__":
    main()