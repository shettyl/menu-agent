"""
Day 5 + 10 + 12: Send menu, grocery, dashboard to Telegram.
Day 12: After successful delivery, persist plan to menu_history sheet.
"""

import os
import json
import time
import mimetypes
import urllib.request
import urllib.parse
import urllib.error
from datetime import date, datetime
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")

PLAN_FILE      = "latest_week_plan.json"
GROCERY_FILE   = "latest_grocery_list.json"
DASHBOARD_FILE = "dashboard.html"

if not BOT_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN not found in .env")
if not CHAT_ID:
    raise RuntimeError("TELEGRAM_CHAT_ID not found in .env")


def load_plan():
    with open(PLAN_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def load_grocery():
    with open(GROCERY_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def load_dish_lookup():
    try:
        from load_data import get_sheet, load_tab
        sheet = get_sheet()
        dishes = load_tab(sheet, "dishes")
        return {d.get("dish_id", ""): d.get("dish_name", "") for d in dishes}
    except Exception as e:
        print(f"  ⚠️  Could not load dish lookup: {e}")
        return {}


def format_week_message(plan, dish_lookup):
    lines = []
    lines.append(f"🗓️ MENU — Week of {plan['week_starting']}")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━")

    for day in plan["days"]:
        training_tag = "  🏃‍♀️" if day.get("is_training_day") else ""
        lines.append("")
        lines.append(f"📆 {day['day_of_week']}, {day['date']}{training_tag}")

        bf = day["breakfast"]
        bf_name = bf.get("dish_name", "-")
        supp = (bf.get("supplement") or "").strip().lower()
        if supp and supp not in ("none", "null", ""):
            lines.append(f"🍳 Breakfast: {bf_name} + {supp}")
        else:
            lines.append(f"🍳 Breakfast: {bf_name}")

        lunch_id = day["lunch"].get("dish_id", "")
        lunch_name = day["lunch"].get("dish_name", "-")
        lines.append(f"🍛 Lunch:     {lunch_name}")

        dinner_id = day["dinner"].get("dish_id", "")
        dinner_name = day["dinner"].get("dish_name", "-")
        is_leftovers = lunch_id and dinner_id and (lunch_id == dinner_id)
        if is_leftovers:
            lines.append(f"🍽️ Dinner:    Lunch leftovers (reheat)")
        else:
            lines.append(f"🍽️ Dinner:    {dinner_name}")

        booster_id = day["dinner"].get("protein_booster_dish_id")
        if booster_id and str(booster_id).lower() not in ("null", "none", ""):
            booster_name = dish_lookup.get(booster_id, booster_id)
            if day.get("is_training_day"):
                lines.append(f"   🥩 Extra protein tonight: {booster_name}")
            else:
                lines.append(f"   💪 Additional high-protein option: {booster_name}")

        fruit = day.get("fruit_of_the_day", "-")
        lines.append(f"🍎 Fruit: {fruit}")

    lines.append("")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("Reply with:")
    lines.append("• 'ok' to confirm this week")
    lines.append("• 'change [day] [meal] to [dish]' to edit")
    return "\n".join(lines)


def format_grocery_message(grocery):
    lines = []
    lines.append(f"🛒 GROCERY LIST — Week of {grocery['week_starting']}")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━")

    sections = [
        ("🥫 SUNDAY — Pantry & dry goods", "sunday_pantry"),
        ("🥦 WEDNESDAY — Fresh veg & herbs", "wednesday_fresh_veg"),
        ("🍗 FRIDAY — Meat, eggs, paneer", "friday_perishables"),
    ]

    for header, key in sections:
        items = grocery.get(key, [])
        lines.append("")
        lines.append(header)
        lines.append("─" * 24)
        if not items:
            lines.append("  (no items)")
            continue
        for item in items:
            name = item.get("item", "?")
            qty = item.get("quantity", "?")
            lines.append(f"  • {name}: {qty}")

    notes = grocery.get("notes", "")
    if notes:
        lines.append("")
        lines.append(f"📝 {notes}")
    return "\n".join(lines)


def send_message(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": CHAT_ID,
        "text": text,
        "disable_web_page_preview": "true",
    }).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    try:
        with urllib.request.urlopen(req) as response:
            result = json.loads(response.read().decode("utf-8"))
            if not result.get("ok"):
                raise RuntimeError(f"Telegram API error: {result}")
            return result
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8")
        raise RuntimeError(f"HTTP {e.code} from Telegram: {error_body}")


def send_document(file_path, caption=""):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendDocument"
    boundary = "----MenuAgentBoundary"
    filename = os.path.basename(file_path)

    with open(file_path, "rb") as f:
        file_bytes = f.read()

    mime, _ = mimetypes.guess_type(file_path)
    mime = mime or "application/octet-stream"

    body_parts = [
        f"--{boundary}".encode("utf-8"),
        b'Content-Disposition: form-data; name="chat_id"',
        b"",
        str(CHAT_ID).encode("utf-8"),
    ]
    if caption:
        body_parts += [
            f"--{boundary}".encode("utf-8"),
            b'Content-Disposition: form-data; name="caption"',
            b"",
            caption.encode("utf-8"),
        ]
    body_parts += [
        f"--{boundary}".encode("utf-8"),
        f'Content-Disposition: form-data; name="document"; filename="{filename}"'.encode("utf-8"),
        f"Content-Type: {mime}".encode("utf-8"),
        b"",
        file_bytes,
        f"--{boundary}--".encode("utf-8"),
    ]

    body = b"\r\n".join(body_parts)
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")

    try:
        with urllib.request.urlopen(req) as resp:
            result = json.loads(resp.read())
            if not result.get("ok"):
                raise RuntimeError(f"Telegram API error: {result}")
            return result
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8")
        raise RuntimeError(f"HTTP {e.code} from Telegram: {error_body}")


def persist_plan_to_history(plan):
    """
    Append every meal from the plan to menu_history in the sheet.
    Schema: week_starting | date | meal_slot | dish_id | actually_eaten
    Called AFTER successful Telegram delivery.
    Idempotent: if rows for this week already exist, skips.
    """
    try:
        from load_data import get_sheet, load_tab
    except Exception as e:
        print(f"  ⚠️  Could not import load_data: {e}")
        return False

    sheet = get_sheet()
    ws = sheet.worksheet("menu_history")

    week_starting = plan.get("week_starting", "")
    if not week_starting:
        print("  ⚠️  Plan has no week_starting; skipping history persist.")
        return False

    # Idempotency check — don't re-add if this week is already logged
    existing = load_tab(sheet, "menu_history")
    already_logged = any(
        str(row.get("week_starting", "")) == week_starting for row in existing
    )
    if already_logged:
        print(f"  ℹ️  History already contains week {week_starting}. Skipping persist.")
        return True

    rows_to_append = []
    for day in plan.get("days", []):
        date_str = day.get("date", "")
        for slot in ("breakfast", "lunch", "dinner"):
            did = day.get(slot, {}).get("dish_id", "")
            if did:
                rows_to_append.append([week_starting, date_str, slot, did, "yes"])
        # Also log the booster if present
        booster = day.get("dinner", {}).get("protein_booster_dish_id", "")
        if booster and str(booster).lower() not in ("null", "none", ""):
            rows_to_append.append([week_starting, date_str, "booster", booster, "yes"])

    if not rows_to_append:
        print("  ⚠️  Nothing to persist.")
        return False

    ws.append_rows(rows_to_append, value_input_option="USER_ENTERED")
    print(f"  ✅ Persisted {len(rows_to_append)} rows to menu_history for week {week_starting}")
    return True


def main():
    print("Loading week plan and grocery list...")
    plan = load_plan()
    grocery = load_grocery()
    dish_lookup = load_dish_lookup()

    print(f"  - Plan: {len(plan['days'])} days")
    total_grocery = sum(
        len(grocery.get(k, []))
        for k in ["sunday_pantry", "wednesday_fresh_veg", "friday_perishables"]
    )
    print(f"  - Grocery: {total_grocery} items")
    print(f"  - Dish lookup: {len(dish_lookup)} dishes\n")

    menu_msg = format_week_message(plan, dish_lookup)
    grocery_msg = format_grocery_message(grocery)

    print("Sending menu message to Telegram...")
    send_message(menu_msg)
    print("  ✅ Menu sent")

    time.sleep(1)

    print("Sending grocery list to Telegram...")
    send_message(grocery_msg)
    print("  ✅ Grocery list sent")

    if os.path.exists(DASHBOARD_FILE):
        print("Sending dashboard file...")
        try:
            send_document(
                DASHBOARD_FILE,
                caption="🎨 Tap to open the week dashboard in your browser"
            )
            print("  ✅ Dashboard sent")
        except Exception as e:
            print(f"  ⚠️  Failed to send dashboard: {e}")
    else:
        print("  ℹ️  No dashboard.html to send")

    # NEW: persist to menu_history after successful delivery
    print("\nPersisting plan to menu_history...")
    try:
        persist_plan_to_history(plan)
    except Exception as e:
        print(f"  ⚠️  Failed to persist history: {e}")

    print("\n🎉 All done.")


if __name__ == "__main__":
    main()