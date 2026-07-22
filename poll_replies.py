"""
Day 7 + Day 8: Poll Telegram for messages, understand them with Gemini, apply edits.
Also handles feedback ratings — writes to Google Sheets 'feedback' tab.
"""

import os
import re
import json
import time
import urllib.request
import urllib.parse
from datetime import datetime, date
from dotenv import load_dotenv
from google import genai
from google.genai.errors import ServerError, ClientError

load_dotenv()

BOT_TOKEN  = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID    = os.getenv("TELEGRAM_CHAT_ID")
LOKESH_ID  = int(os.getenv("LOKESH_USER_ID", "0"))
ANITHA_ID  = int(os.getenv("ANITHA_USER_ID", "0"))
GEMINI_KEY = os.getenv("GEMINI_API_KEY")

STATE_FILE = "state.json"
PLAN_FILE  = "latest_week_plan.json"
MODEL_NAME = "gemini-2.5-flash-lite"

ALLOWED_USERS = {LOKESH_ID, ANITHA_ID}

MENU_KEYWORDS = {
    "change", "swap", "replace", "instead",
    "breakfast", "lunch", "dinner", "meal",
    "monday", "tuesday", "wednesday", "thursday",
    "friday", "saturday", "sunday",
    "mon", "tue", "wed", "thu", "fri", "sat", "sun",
    "ok", "okay", "confirm", "approved", "sounds good", "looks good",
    "👍", "✅", "🙌",
    "menu", "week", "cook", "dish", "plan", "grocery",
    "rating", "rate", "skip",
}

RATING_RE = re.compile(r"\b([1-5])\D+([1-5])\D+([1-5])\b")


# =========================================================
# State helpers
# =========================================================

def load_state():
    if not os.path.exists(STATE_FILE):
        return {"last_update_id": 0}
    with open(STATE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


# =========================================================
# Telegram helpers
# =========================================================

def fetch_updates(offset):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates?offset={offset}&timeout=10"
    with urllib.request.urlopen(url) as resp:
        data = json.loads(resp.read())
    if not data.get("ok"):
        raise RuntimeError(f"Telegram error: {data}")
    return data.get("result", [])


def send_reply(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": CHAT_ID,
        "text": text,
    }).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8")
        raise RuntimeError(f"HTTP {e.code}: {body}")


# =========================================================
# Plan helpers
# =========================================================

def load_plan():
    if not os.path.exists(PLAN_FILE):
        return None
    with open(PLAN_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_plan(plan):
    with open(PLAN_FILE, "w", encoding="utf-8") as f:
        json.dump(plan, f, indent=2)


def find_day(plan, day_of_week):
    day_of_week = day_of_week.strip().lower()
    for day in plan["days"]:
        if day["day_of_week"].lower() == day_of_week:
            return day
    return None


# =========================================================
# Gemini call with retry
# =========================================================

def call_gemini(client, prompt):
    for attempt in range(3):
        try:
            return client.models.generate_content(
                model=MODEL_NAME,
                contents=prompt,
            )
        except ServerError:
            wait = 5 * (2 ** attempt)
            print(f"   Gemini busy; waiting {wait}s")
            time.sleep(wait)
        except ClientError as e:
            if "429" in str(e):
                print(f"   Rate limited; waiting 30s")
                time.sleep(30)
            else:
                raise
    raise RuntimeError("Gemini unavailable")


# =========================================================
# Pre-filter and intent classifier
# =========================================================

def looks_menu_related(text):
    lowered = text.lower()
    return any(kw in lowered for kw in MENU_KEYWORDS)


def parse_message(client, message_text, current_plan_summary):
    prompt = f"""You are parsing a family member's Telegram reply about their weekly menu.
The current menu plan is shown below. The user may want to change a dish, confirm the plan,
or say something unrelated.

CURRENT MENU PLAN (summarized):
{current_plan_summary}

USER MESSAGE:
"{message_text}"

Classify the message intent as ONE of:
- "confirm"       : user is accepting the plan as-is (e.g. "ok", "looks good", "confirm", "👍", "thanks")
- "change_dish"   : user wants to change one specific meal to a different dish
- "swap_dishes"   : user wants to swap two meals with each other
- "unclear"       : you are not confident what they want
- "ignore"        : the message is not about the menu (chit-chat, jokes, etc.)

If intent is "change_dish", extract:
- day       : one of Monday/Tuesday/Wednesday/Thursday/Friday/Saturday/Sunday
- meal      : one of breakfast / lunch / dinner
- new_dish  : the dish name they want (natural language ok, don't invent)

If intent is "swap_dishes", extract:
- day1, meal1, day2, meal2

Return STRICT JSON. No markdown. Example outputs:

{{"intent": "change_dish", "day": "Wednesday", "meal": "dinner", "new_dish": "dal tadka"}}
{{"intent": "confirm"}}
{{"intent": "swap_dishes", "day1": "Tuesday", "meal1": "lunch", "day2": "Thursday", "meal2": "lunch"}}
{{"intent": "unclear", "reason": "user mentioned dinner but didn't say which day"}}
{{"intent": "ignore"}}

Now parse this message. Output ONLY the JSON:
"""
    response = call_gemini(client, prompt)
    raw = response.text.strip()
    if raw.startswith("```"):
        raw = raw.strip("`").lstrip("json").strip()
    return json.loads(raw)


def summarize_plan(plan):
    lines = [f"Week of {plan['week_starting']}:"]
    for day in plan["days"]:
        lines.append(
            f"- {day['day_of_week']}: "
            f"BF={day['breakfast'].get('dish_name', '?')}, "
            f"L={day['lunch'].get('dish_name', '?')}, "
            f"D={day['dinner'].get('dish_name', '?')}"
        )
    return "\n".join(lines)


# =========================================================
# Dish matching with confidence
# =========================================================

def find_dish_by_id_literal(dishes, hint):
    hint = hint.strip().upper()
    if hint.startswith("D") and hint[1:].isdigit():
        for d in dishes:
            if d.get("dish_id", "").upper() == hint:
                return d.get("dish_id")
    return None


def find_dish_id_by_name(client, dishes, name_hint):
    explicit = find_dish_by_id_literal(dishes, name_hint)
    if explicit:
        return explicit, "high", []

    dish_list = "\n".join(f"[{d['dish_id']}] {d['dish_name']}" for d in dishes)
    prompt = f"""From this dish catalog, find the ONE dish_id that best matches the user's request.
Then rate your confidence in the match.

Catalog:
{dish_list}

User asked for: "{name_hint}"

Guidelines:
- "high" confidence: user's words clearly identify this exact dish
- "low" confidence: user's words are vague or match multiple dishes
- "none": no dish reasonably matches

Return STRICT JSON. Examples:
{{"dish_id": "D007", "confidence": "high"}}
{{"dish_id": "D013", "confidence": "low", "alternates": ["D014", "D015"]}}
{{"dish_id": null, "confidence": "none"}}

Output ONLY the JSON:
"""
    response = call_gemini(client, prompt)
    raw = response.text.strip()
    if raw.startswith("```"):
        raw = raw.strip("`").lstrip("json").strip()
    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        return None, "none", []
    return (
        result.get("dish_id"),
        result.get("confidence", "none"),
        result.get("alternates", []),
    )


# =========================================================
# Apply edits
# =========================================================

def apply_change_dish(client, plan, dishes, parsed):
    day = find_day(plan, parsed["day"])
    if not day:
        return f"❌ Couldn't find day '{parsed['day']}'. Nothing changed."
    meal = parsed["meal"].lower()
    if meal not in ("breakfast", "lunch", "dinner"):
        return f"❌ '{parsed['meal']}' isn't a valid meal. Nothing changed."

    new_dish_id, confidence, alternates = find_dish_id_by_name(
        client, dishes, parsed["new_dish"]
    )

    if confidence == "none" or not new_dish_id:
        return (
            f"⚠️ I couldn't find a dish matching '{parsed['new_dish']}' in the catalog. "
            f"Nothing changed. Try being more specific."
        )

    if confidence == "low":
        primary = next((d for d in dishes if d["dish_id"] == new_dish_id), None)
        msg = (
            f"🤔 '{parsed['new_dish']}' is a bit vague — closest match I found is:\n"
            f"  • {primary['dish_name']} ({new_dish_id})\n"
        )
        if alternates:
            alt_lines = []
            for aid in alternates[:3]:
                alt = next((d for d in dishes if d["dish_id"] == aid), None)
                if alt:
                    alt_lines.append(f"  • {alt['dish_name']} ({aid})")
            if alt_lines:
                msg += "Other possibilities:\n" + "\n".join(alt_lines) + "\n"
        msg += (
            f"\nNothing changed yet. Reply with the exact dish name, "
            f"or the dish_id (e.g. 'change {day['day_of_week']} {meal} to {new_dish_id}')."
        )
        return msg

    new_dish = next((d for d in dishes if d["dish_id"] == new_dish_id), None)
    if not new_dish:
        return f"❌ Dish '{new_dish_id}' not in catalog. Nothing changed."

    old_name = day[meal].get("dish_name", "?")
    day[meal]["dish_id"]   = new_dish_id
    day[meal]["dish_name"] = new_dish["dish_name"]
    day[meal]["reasoning"] = f"Changed by user request"
    save_plan(plan)
    return f"✅ {day['day_of_week']} {meal}: {old_name} → {new_dish['dish_name']}"


def apply_swap(plan, parsed):
    d1 = find_day(plan, parsed["day1"])
    d2 = find_day(plan, parsed["day2"])
    if not d1 or not d2:
        return "❌ Couldn't find one of those days. Nothing swapped."
    m1 = parsed["meal1"].lower()
    m2 = parsed["meal2"].lower()
    if m1 not in ("breakfast", "lunch", "dinner"):
        return f"❌ '{parsed['meal1']}' isn't a valid meal. Nothing swapped."
    if m2 not in ("breakfast", "lunch", "dinner"):
        return f"❌ '{parsed['meal2']}' isn't a valid meal. Nothing swapped."

    old_name_1 = d1[m1].get("dish_name", "?")
    old_name_2 = d2[m2].get("dish_name", "?")
    d1[m1], d2[m2] = d2[m2], d1[m1]
    save_plan(plan)
    return (
        f"✅ Swapped:\n"
        f"  {d1['day_of_week']} {m1}: {old_name_1} ↔ {old_name_2}\n"
        f"  {d2['day_of_week']} {m2}: {old_name_2} ↔ {old_name_1}"
    )


# =========================================================
# Day 8: Ratings
# =========================================================

def try_parse_rating(text):
    """
    Try to extract 3 ratings from a message.
    Returns (bf, lunch, dinner) as ints, or 'skip', or None.
    """
    lowered = text.strip().lower()
    if lowered in ("skip", "no", "pass"):
        return "skip"
    m = RATING_RE.search(lowered)
    if m:
        return tuple(int(m.group(i)) for i in (1, 2, 3))
    return None


def save_rating_to_sheet(plan, ratings):
    """
    Persist today's ratings to Google Sheets 'feedback' tab.
    Returns True on success, False if today isn't in the plan.
    Raises on real errors (sheet write failure).
    """
    from load_data import get_sheet
    sheet = get_sheet()
    ws = sheet.worksheet("feedback")

    today = date.today().isoformat()
    day = None
    for d in plan["days"]:
        if d.get("date") == today:
            day = d
            break

    if not day:
        print(f"   No plan entry for today ({today}).")
        return False

    slots = [("breakfast", ratings[0]), ("lunch", ratings[1]), ("dinner", ratings[2])]
    for meal_slot, rating in slots:
        dish_id = day[meal_slot].get("dish_id", "")
        if not dish_id:
            continue
        row = [today, meal_slot, dish_id, int(rating), ""]
        ws.append_row(row, value_input_option="USER_ENTERED")
        print(f"   Wrote rating: {meal_slot} = {rating} for {dish_id}")
    return True


# =========================================================
# Main message handler
# =========================================================

def handle_message(client, plan, dishes, message):
    user_id = message.get("from", {}).get("id")
    if user_id not in ALLOWED_USERS:
        return None

    text = message.get("text", "").strip()
    if not text:
        return None

    if message.get("from", {}).get("is_bot"):
        return None

    print(f"📨 Message from {user_id}: {text[:80]}")

    # Rating check FIRST — before keyword filter
    # (rating messages are numbers like "4,3,5" and won't match menu keywords)
    rating = try_parse_rating(text)
    if rating == "skip":
        print("   User skipped feedback")
        return "👍 No feedback today, noted."
    if isinstance(rating, tuple):
        print(f"   Detected rating: {rating}")
        try:
            saved = save_rating_to_sheet(plan, rating)
            if saved:
                return f"✅ Thanks! Ratings saved: BF={rating[0]}, Lunch={rating[1]}, Dinner={rating[2]}"
            else:
                today_str = date.today().isoformat()
                return (
                    f"🤔 Got your rating ({rating[0]},{rating[1]},{rating[2]}), "
                    f"but today ({today_str}) isn't in the current menu plan. "
                    f"Rating not saved."
                )
        except Exception as e:
            print(f"   Failed to save rating: {e}")
            return f"⚠️ Got your rating but couldn't save it: {e}"

    # Now the keyword filter for edit intents
    if not looks_menu_related(text):
        print(f"   Skipped (no menu keywords)")
        return None

    summary = summarize_plan(plan)
    try:
        parsed = parse_message(client, text, summary)
    except Exception as e:
        print(f"   Failed to parse: {e}")
        return None

    intent = parsed.get("intent")
    print(f"   Intent: {intent}")

    if intent == "ignore":
        return None
    if intent == "confirm":
        return "👍 Got it — week confirmed! Grocery list stays the same."
    if intent == "unclear":
        reason = parsed.get("reason", "not sure what to change")
        return (
            f"🤔 Not sure I understood — {reason}.\n"
            f"Try: 'change [day] [meal] to [dish]', "
            f"'swap [day1] [meal1] with [day2] [meal2]', or 'ok' to confirm."
        )
    if intent == "change_dish":
        return apply_change_dish(client, plan, dishes, parsed)
    if intent == "swap_dishes":
        return apply_swap(plan, parsed)

    return None


def main():
    print(f"🔄 Poll run at {datetime.now().isoformat()}")

    if not all([BOT_TOKEN, CHAT_ID, GEMINI_KEY, LOKESH_ID, ANITHA_ID]):
        raise RuntimeError("Missing one of the required env vars.")

    plan = load_plan()
    if not plan:
        print("No plan file yet — nothing to edit against.")
        return

    from load_data import get_sheet, load_tab
    print("Loading dish catalog from sheet...")
    dishes = load_tab(get_sheet(), "dishes")
    print(f"  {len(dishes)} dishes loaded.")

    state = load_state()
    offset = state.get("last_update_id", 0) + 1
    print(f"Fetching updates since {offset}...")
    updates = fetch_updates(offset)
    print(f"  {len(updates)} new updates.")

    if not updates:
        print("Nothing to do.")
        return

    client = genai.Client(api_key=GEMINI_KEY)

    processed_ids = []
    for update in updates:
        update_id = update.get("update_id")
        message = update.get("message")
        if not message:
            processed_ids.append(update_id)
            continue

        try:
            reply = handle_message(client, plan, dishes, message)
        except Exception as e:
            print(f"   Error handling message: {e}")
            reply = None

        if reply:
            try:
                send_reply(reply)
                print(f"   Replied: {reply[:120]}")
            except Exception as e:
                print(f"   Failed to send reply: {e}")

        processed_ids.append(update_id)

    if processed_ids:
        state["last_update_id"] = max(processed_ids)
        save_state(state)
        print(f"State updated. last_update_id = {state['last_update_id']}")


if __name__ == "__main__":
    main()