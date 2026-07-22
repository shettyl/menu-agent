"""
Day 10: Generate a single-file HTML dashboard from the current plan + grocery list.

Reads latest_week_plan.json and latest_grocery_list.json,
produces dashboard.html — a mobile-first, no-JavaScript view of the week.

Also reads recent feedback ratings and shows them per dish.

Special handling: when dinner reuses lunch's dish_id, show it as "Lunch leftovers"
instead of repeating the name, and elevate the protein booster.
"""

import os
import json
from datetime import datetime, date
from load_data import get_sheet, load_tab

PLAN_FILE      = "latest_week_plan.json"
GROCERY_FILE   = "latest_grocery_list.json"
OUTPUT_FILE    = "dashboard.html"


def load_json(path):
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_dish_lookup():
    """Return dict of dish_id -> dish_name for looking up booster names."""
    sheet = get_sheet()
    dishes = load_tab(sheet, "dishes")
    return {d.get("dish_id", ""): d.get("dish_name", "") for d in dishes}


def load_recent_ratings():
    """Return dict of dish_id -> average rating."""
    sheet = get_sheet()
    feedback = load_tab(sheet, "feedback")
    dish_ratings = {}
    for row in feedback:
        try:
            did = row.get("dish_id", "")
            rating = int(row.get("rating", 0))
            if not did or rating < 1 or rating > 5:
                continue
            dish_ratings.setdefault(did, []).append(rating)
        except (ValueError, TypeError):
            continue
    return {
        did: round(sum(r) / len(r), 1)
        for did, r in dish_ratings.items()
    }


def rating_stars(avg):
    if avg is None:
        return ""
    full = int(round(avg))
    return "⭐" * full + "☆" * (5 - full)


DAY_EMOJI = {
    "Monday":    "🌄",
    "Tuesday":   "🌅",
    "Wednesday": "☀️",
    "Thursday":  "🌤️",
    "Friday":    "🌇",
    "Saturday":  "🎉",
    "Sunday":    "🕊️",
}


def render_day_card(day, dish_ratings, dish_lookup):
    day_name = day["day_of_week"]
    date_str = day.get("date", "")
    emoji    = DAY_EMOJI.get(day_name, "📅")
    is_training = day.get("is_training_day")
    training = "🏃‍♀️ training day" if is_training else ""

    def meal_row(icon, label, meal_dict):
        did = meal_dict.get("dish_id", "")
        name = meal_dict.get("dish_name", "?")
        supplement = (meal_dict.get("supplement") or "").strip().lower()
        if supplement and supplement not in ("none", "null", ""):
            name += f" <span class='supp'>+ {supplement}</span>"
        avg = dish_ratings.get(did)
        stars = f"<span class='stars'>{rating_stars(avg)}</span>" if avg else ""
        return f"""
        <div class="meal">
          <span class="meal-icon">{icon}</span>
          <span class="meal-label">{label}</span>
          <span class="meal-name">{name} {stars}</span>
        </div>
        """

    # Special dinner handling: if dinner reuses lunch's dish_id, show as leftovers
    lunch_id = day["lunch"].get("dish_id", "")
    dinner_id = day["dinner"].get("dish_id", "")
    is_leftovers = lunch_id and dinner_id and (lunch_id == dinner_id)

    if is_leftovers:
        dinner_html = f"""
        <div class="meal leftovers">
          <span class="meal-icon">🍽️</span>
          <span class="meal-label">Dinner</span>
          <span class="meal-name">Lunch leftovers <span class="leftover-tag">reheat</span></span>
        </div>
        """
    else:
        dinner_html = meal_row("🍽️", "Dinner", day["dinner"])

    # Protein booster — always resolve to full dish name
    booster_id = day["dinner"].get("protein_booster_dish_id")
    booster_html = ""
    if booster_id and str(booster_id).lower() not in ("null", "none", ""):
        booster_name = dish_lookup.get(booster_id, booster_id)
        avg = dish_ratings.get(booster_id)
        stars = f" <span class='stars'>{rating_stars(avg)}</span>" if avg else ""
        booster_class = "booster booster-training" if is_training else "booster"
        prefix = "🥩 Extra protein tonight" if is_training else "💪 Additional high-protein option"
        booster_html = f"""
        <div class="{booster_class}">
          {prefix}: <strong>{booster_name}</strong>{stars}
        </div>
        """

    fruit = day.get("fruit_of_the_day", "")
    fruit_html = f'<div class="fruit">🍎 {fruit}</div>' if fruit else ""

    return f"""
    <div class="day-card">
      <div class="day-header">
        <span class="day-emoji">{emoji}</span>
        <span class="day-name">{day_name}</span>
        <span class="day-date">{date_str}</span>
        {f'<span class="training-tag">{training}</span>' if training else ''}
      </div>
      {meal_row("🍳", "Breakfast", day["breakfast"])}
      {meal_row("🍛", "Lunch",     day["lunch"])}
      {dinner_html}
      {booster_html}
      {fruit_html}
    </div>
    """


def render_grocery_section(title, icon, items):
    if not items:
        item_html = "<em>(no items)</em>"
    else:
        item_html = "\n".join(
            f'<li><span class="item">{item.get("item","?")}</span>'
            f'<span class="qty">{item.get("quantity","?")}</span></li>'
            for item in items
        )
    return f"""
    <div class="grocery-section">
      <h3>{icon} {title}</h3>
      <ul>{item_html}</ul>
    </div>
    """


def render_html(plan, grocery, dish_ratings, dish_lookup):
    week_starting = plan.get("week_starting", "?")
    generated_at = datetime.now().strftime("%d %b %Y, %I:%M %p")

    days_html = "\n".join(render_day_card(d, dish_ratings, dish_lookup) for d in plan["days"])

    grocery_html = ""
    if grocery:
        grocery_html += render_grocery_section(
            "Sunday — Pantry & dry goods", "🥫",
            grocery.get("sunday_pantry", [])
        )
        grocery_html += render_grocery_section(
            "Wednesday — Fresh veg & herbs", "🥦",
            grocery.get("wednesday_fresh_veg", [])
        )
        grocery_html += render_grocery_section(
            "Friday — Meat, eggs, paneer", "🍗",
            grocery.get("friday_perishables", [])
        )
        notes = grocery.get("notes", "")
        if notes:
            grocery_html += f'<div class="notes">📝 {notes}</div>'

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<meta name="theme-color" content="#0f766e"/>
<title>Shetty Family Menu — Week of {week_starting}</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    margin: 0; padding: 0;
    background: #f5f5f4;
    color: #1c1917;
    line-height: 1.4;
  }}
  .container {{ max-width: 720px; margin: 0 auto; padding: 16px; }}
  header {{
    background: linear-gradient(135deg, #0f766e, #0e7490);
    color: white;
    padding: 24px 16px;
    text-align: center;
  }}
  header h1 {{ margin: 0; font-size: 22px; font-weight: 600; }}
  header .subtitle {{ margin-top: 4px; font-size: 14px; opacity: 0.9; }}
  h2 {{ font-size: 18px; margin: 24px 0 12px; color: #0f766e; }}
  .day-card {{
    background: white;
    border-radius: 12px;
    padding: 14px 16px;
    margin-bottom: 12px;
    box-shadow: 0 1px 3px rgba(0,0,0,0.05);
  }}
  .day-header {{
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 10px;
    border-bottom: 1px solid #e7e5e4;
    padding-bottom: 8px;
    flex-wrap: wrap;
  }}
  .day-emoji {{ font-size: 20px; }}
  .day-name {{ font-weight: 600; font-size: 16px; }}
  .day-date {{ color: #78716c; font-size: 13px; }}
  .training-tag {{
    background: #fef3c7;
    color: #92400e;
    padding: 2px 8px;
    border-radius: 12px;
    font-size: 12px;
    font-weight: 500;
  }}
  .meal {{ display: flex; align-items: baseline; gap: 8px; padding: 4px 0; font-size: 14px; }}
  .meal-icon {{ font-size: 15px; }}
  .meal-label {{ font-weight: 500; color: #57534e; width: 68px; flex-shrink: 0; }}
  .meal-name {{ flex: 1; }}
  .supp {{ background: #dbeafe; color: #1e40af; padding: 1px 6px; border-radius: 6px; font-size: 12px; }}
  .leftovers .meal-name {{ color: #78716c; font-style: italic; }}
  .leftover-tag {{
    background: #f3f4f6; color: #57534e;
    padding: 1px 6px; border-radius: 6px; font-size: 11px;
    margin-left: 4px;
  }}
  .stars {{ font-size: 12px; color: #d97706; margin-left: 6px; }}
  .booster {{
    margin-top: 6px; padding: 8px 12px; border-radius: 8px;
    background: #ecfdf5; color: #065f46;
    font-size: 13px;
    border-left: 3px solid #10b981;
  }}
  .booster-training {{
    background: #fef2f2; color: #991b1b;
    border-left: 3px solid #ef4444;
    font-weight: 500;
  }}
  .fruit {{
    margin-top: 6px; padding: 6px 10px; border-radius: 8px;
    background: #f0fdf4; color: #166534; font-size: 13px;
  }}
  .grocery-section {{
    background: white;
    border-radius: 12px;
    padding: 14px 16px;
    margin-bottom: 12px;
    box-shadow: 0 1px 3px rgba(0,0,0,0.05);
  }}
  .grocery-section h3 {{ margin: 0 0 8px; font-size: 15px; color: #0f766e; }}
  .grocery-section ul {{ list-style: none; padding: 0; margin: 0; }}
  .grocery-section li {{
    display: flex; justify-content: space-between;
    padding: 6px 0;
    border-bottom: 1px solid #f5f5f4;
    font-size: 14px;
  }}
  .grocery-section li:last-child {{ border-bottom: none; }}
  .grocery-section .item {{ color: #1c1917; }}
  .grocery-section .qty {{ color: #57534e; font-weight: 500; }}
  .notes {{
    background: #fef9c3; color: #713f12;
    padding: 10px 14px; border-radius: 8px; font-size: 13px;
    margin-top: 8px;
  }}
  footer {{
    text-align: center;
    color: #a8a29e;
    font-size: 12px;
    padding: 20px 16px;
  }}
</style>
</head>
<body>
  <header>
    <h1>🍽️ Shetty Family Menu</h1>
    <div class="subtitle">Week of {week_starting}</div>
  </header>
  <div class="container">
    <h2>📅 This week</h2>
    {days_html}

    <h2>🛒 Grocery list</h2>
    {grocery_html}
  </div>
  <footer>Generated {generated_at} · menu-agent</footer>
</body>
</html>
"""


def main():
    print("🎨 Rendering dashboard...")

    plan = load_json(PLAN_FILE)
    if not plan:
        raise RuntimeError(f"No {PLAN_FILE} found. Run plan_week.py first.")
    grocery = load_json(GROCERY_FILE)

    print("Loading dish catalog + recent ratings...")
    try:
        dish_lookup = load_dish_lookup()
        print(f"  {len(dish_lookup)} dishes loaded for name lookup.")
    except Exception as e:
        print(f"  ⚠️  Could not load dish catalog: {e}")
        dish_lookup = {}

    try:
        dish_ratings = load_recent_ratings()
        print(f"  {len(dish_ratings)} dishes have ratings.")
    except Exception as e:
        print(f"  ⚠️  Could not load ratings: {e}")
        dish_ratings = {}

    html = render_html(plan, grocery, dish_ratings, dish_lookup)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"✅ Wrote {OUTPUT_FILE} ({len(html)} bytes)")


if __name__ == "__main__":
    main()