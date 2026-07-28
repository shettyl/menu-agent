"""
Day 7-10 + Day 11: Poll Telegram for messages, understand them with Gemini, apply edits.
Also handles feedback ratings and quiet resend of updated menu + dashboard.

Day 11 additions:
- Explicit dates in edit confirmations ("Thursday, Jul 30")
- Quiet auto-resend: after any edit, wait until 15 min of silence, then send updated menu + dashboard
- Explicit "show menu" / "resend" / "latest" trigger for immediate resend
"""

import os
import re
import sys
import json
import time
import subprocess
import urllib.request
import urllib.parse
from datetime import datetime, date, timedelta
from dotenv import load_dotenv
from google import genai
from google.genai.errors import ServerError, ClientError

load_dotenv()

BOT_TOKEN  = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID    = os.getenv("TELEGRAM_CHAT_ID")
LOKESH_ID  = int(os.getenv("LOKESH_USER_ID", "0"))
ANITHA_ID  = int(os.getenv("ANITHA_USER_ID", "0"))
GEMINI_KEY = os.getenv("GEMINI_API_KEY")

STATE_FILE     = "state.json"
PLAN_FILE      = "latest_week_plan.json"
DASHBOARD_FILE = "dashboard.html"
MODEL_NAME     = "gemini-2.5-flash-lite"
QUIET_RESEND_MINUTES = 15  # wait this long after last edit before auto-resend

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
    "show", "latest", "resend", "current",
}

RATING_RE = re.compile(r"\b([1-5])\D+([1-5])\D+([1-5])\b")
RESEND_KEYWORDS = {"show menu", "resend", "latest menu", "current menu", "show current menu"}


# =========================================================
# State helpers
# =========================================================

def load_state():
    if not os.path.exists(STATE_FILE):
        return {"last_update_id": 0, "last_edit_at": None, "pending_resend": False}
    with open(STATE_FILE, "r", encoding="utf-8") as f:
        state = json.load(f)
    # Ensure new fields exist for older state files
    state.setdefault("last_edit_at", None)
    state.setdefault("pending_resend", False)
    return state


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


def format_day_label(day_dict):
    """
    Return a display label like 'Thursday (Jul 30)' from a day dict.
    Portable across OS (avoids %-d which is Linux-only).
    """
    day_name = day_dict.get("day_of_week", "?")
    date_str = day_dict.get("date", "")
    if not date_str:
        return day_name
    try:
        d = date.fromisoformat(date_str)
        month_abbr = d.strftime("%b")  # e.g. 'Jul'
        day_num = str(d.day)           # '30' (no leading zero)
        return f"{day_name} ({month_abbr} {day_num})"
    except (ValueError, TypeError):
        return day_name


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
# Pre-filter, intent, dish matching
# =========================================================

def looks_menu_related(text):
    lowered = text.lower()
    return any(kw in lowered for kw in MENU_KEYWORDS)


def is_resend_request(text):
    lowered = text.lower().strip()
    return any(kw in lowered for kw in RESEND_KEYWORDS)


def parse_message(client, message_text, current_plan_summary):
    prompt = f"""You are parsing a family member's Telegram reply about their weekly menu.
The current menu plan is shown below.

CURRENT MENU PLAN (summarized):
{current_plan_summary}

USER MESSAGE:
"{message_text}"

Classify the message intent as ONE of:
- "confirm"       : user is accepting the plan as-is
- "change_dish"   : user wants to change one specific meal to a different dish
- "swap_dishes"   : user wants to swap two meals with each other
- "unclear"       : you are not confident what they want
- "ignore"        : the message is not about the menu

If intent is "change_dish", extract: day, meal, new_dish
If intent is "swap_dishes", extract: day1, meal1, day2, meal2

Return STRICT JSON. No markdown. Example outputs:
{{"intent": "change_dish", "day": "Wednesday", "meal": "dinner", "new_dish": "dal tadka"}}
{{"intent": "confirm"}}
{{"intent": "swap_dishes", "day1": "Tuesday", "meal1": "lunch", "day2": "Thursday", "meal2": "lunch"}}
{{"intent": "unclear", "reason": "..."}}
{{"intent": "ignore"}}

Output ONLY the JSON:
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
            f"- {day['day_of_week']} ({day.get('date','')}): "
            f"BF={day['breakfast'].get('dish_name', '?')}, "
            f"L={day['lunch'].get('dish_name', '?')}, "
            f"D={day['dinner'].get('dish_name', '?')}"
        )
    return "\n".join(lines)


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
Then rate your confidence.

Catalog:
{dish_list}

User asked for: "{name_hint}"

Guidelines:
- "high": user's words clearly identify this exact dish
- "low": user's words are vague or match multiple dishes
- "none": no dish reasonably matches

Return STRICT JSON:
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
# Apply edits (with explicit-date confirmations)
# =========================================================

def apply_change_dish(client, plan, dishes, parsed):
    day = find_day(plan, parsed["day"])
    if not day:
        return None, f"❌ Couldn't find day '{parsed['day']}'. Nothing changed."
    meal = parsed["meal"].lower()
    if meal not in ("breakfast", "lunch", "dinner"):
        return None, f"❌ '{parsed['meal']}' isn't a valid meal. Nothing changed."

    new_dish_id, confidence, alternates = find_dish_id_by_name(client, dishes, parsed["new_dish"])

    if confidence == "none" or not new_dish_id:
        return None, (
            f"⚠️ I couldn't find a dish matching '{parsed['new_dish']}'. "
            f"Nothing changed. Try being more specific."
        )

    if confidence == "low":
        primary = next((d for d in dishes if d["dish_id"] == new_dish_id), None)
        day_label = format_day_label(day)
        msg = (
            f"🤔 '{parsed['new_dish']}' is vague — closest match:\n"
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
            f"\nNothing changed yet. Reply with the exact dish name or dish_id "
            f"(e.g. 'change {day_label} {meal} to {new_dish_id}')."
        )
        return None, msg

    new_dish = next((d for d in dishes if d["dish_id"] == new_dish_id), None)
    if not new_dish:
        return None, f"❌ Dish '{new_dish_id}' not in catalog. Nothing changed."

    old_name = day[meal].get("dish_name", "?")
    day[meal]["dish_id"]   = new_dish_id
    day[meal]["dish_name"] = new_dish["dish_name"]
    day[meal]["reasoning"] = "Changed by user request"
    save_plan(plan)
    day_label = format_day_label(day)
    return True, f"✅ {day_label} {meal}: {old_name} → {new_dish['dish_name']}"


def apply_swap(plan, parsed):
    d1 = find_day(plan, parsed["day1"])
    d2 = find_day(plan, parsed["day2"])
    if not d1 or not d2:
        return None, "❌ Couldn't find one of those days. Nothing swapped."
    m1 = parsed["meal1"].lower()
    m2 = parsed["meal2"].lower()
    if m1 not in ("breakfast", "lunch", "dinner"):
        return None, f"❌ '{parsed['meal1']}' isn't a valid meal. Nothing swapped."
    if m2 not in ("breakfast", "lunch", "dinner"):
        return None, f"❌ '{parsed['meal2']}' isn't a valid meal. Nothing swapped."

    old_name_1 = d1[m1].get("dish_name", "?")
    old_name_2 = d2[m2].get("dish_name", "?")
    d1[m1], d2[m2] = d2[m2], d1[m1]
    save_plan(plan)

    label1 = format_day_label(d1)
    label2 = format_day_label(d2)
    return True, (
        f"✅ Swapped:\n"
        f"  {label1} {m1}: {old_name_1} ↔ {old_name_2}\n"
        f"  {label2} {m2}: {old_name_2} ↔ {old_name_1}"
    )


# =========================================================
# Ratings
# =========================================================

def try_parse_rating(text):
    lowered = text.strip().lower()
    if lowered in ("skip", "no", "pass"):
        return "skip"
    m = RATING_RE.search(lowered)
    if m:
        return tuple(int(m.group(i)) for i in (1, 2, 3))
    return None


def save_rating_to_sheet(plan, ratings):
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
# Resend logic (dashboard regen + send full update)
# =========================================================

def regenerate_dashboard():
    """Run render_dashboard.py to refresh dashboard.html."""
    try:
        result = subprocess.run(
            ["python", "render_dashboard.py"],
            capture_output=True, text=True, timeout=60
        )
        if result.returncode != 0:
            print(f"   ⚠️  Dashboard regen failed: {result.stderr}")
            return False
        return True
    except Exception as e:
        print(f"   ⚠️  Dashboard regen error: {e}")
        return False


def send_updated_menu_and_dashboard(plan):
    """Send the full updated menu message + updated dashboard.html."""
    try:
        from send_to_telegram import (
            format_week_message, send_message, send_document, load_dish_lookup
        )
        dish_lookup = load_dish_lookup()
        menu_msg = format_week_message(plan, dish_lookup)
        header = "📋 UPDATED MENU (after recent edits)\n\n"
        send_message(header + menu_msg)
        time.sleep(1)
        if os.path.exists(DASHBOARD_FILE):
            send_document(
                DASHBOARD_FILE,
                caption="🎨 Updated dashboard — tap to open"
            )
        return True
    except Exception as e:
        print(f"   ⚠️  Failed to send updated menu: {e}")
        return False


def do_full_resend(plan):
    """Regenerate dashboard, then send updated menu + dashboard to family group."""
    print("📤 Regenerating dashboard and sending updated menu...")
    regenerate_dashboard()
    return send_updated_menu_and_dashboard(plan)


# =========================================================
# Main message handler
# =========================================================

def handle_message(client, plan, dishes, message, state):
    """
    Returns (reply_text, edit_applied_bool).
    edit_applied_bool tells main() whether to update pending_resend flag.
    """
    user_id = message.get("from", {}).get("id")
    if user_id not in ALLOWED_USERS:
        return None, False

    text = message.get("text", "").strip()
    if not text:
        return None, False

    if message.get("from", {}).get("is_bot"):
        return None, False

    print(f"📨 Message from {user_id}: {text[:80]}")

    # Rating check first — pure regex, no LLM
    rating = try_parse_rating(text)
    if rating == "skip":
        print("   User skipped feedback")
        return "👍 No feedback today, noted.", False
    if isinstance(rating, tuple):
        print(f"   Detected rating: {rating}")
        try:
            saved = save_rating_to_sheet(plan, rating)
            if saved:
                return (
                    f"✅ Thanks! Ratings saved: BF={rating[0]}, "
                    f"Lunch={rating[1]}, Dinner={rating[2]}"
                ), False
            else:
                today_str = date.today().isoformat()
                return (
                    f"🤔 Got your rating ({rating[0]},{rating[1]},{rating[2]}), "
                    f"but today ({today_str}) isn't in the current plan. Rating not saved."
                ), False
        except Exception as e:
            print(f"   Failed to save rating: {e}")
            return f"⚠️ Got your rating but couldn't save it: {e}", False

    # Explicit resend request — do immediately
    if is_resend_request(text):
        print("   Explicit resend requested")
        do_full_resend(plan)
        # After a manual resend, clear the pending flag
        state["pending_resend"] = False
        return None, False

    # Keyword pre-filter
    if not looks_menu_related(text):
        print(f"   Skipped (no menu keywords)")
        return None, False

    # Otherwise it's an edit/confirm/unclear intent
    summary = summarize_plan(plan)
    try:
        parsed = parse_message(client, text, summary)
    except Exception as e:
        print(f"   Failed to parse: {e}")
        return None, False

    intent = parsed.get("intent")
    print(f"   Intent: {intent}")

    if intent == "ignore":
        return None, False
    if intent == "confirm":
        return "👍 Got it — week confirmed! Grocery list stays the same.", False
    if intent == "unclear":
        reason = parsed.get("reason", "not sure what to change")
        return (
            f"🤔 Not sure I understood — {reason}.\n"
            f"Try: 'change [day] [meal] to [dish]', "
            f"'swap [day1] [meal1] with [day2] [meal2]', "
            f"'show menu' to see the latest, or 'ok' to confirm."
        ), False
    if intent == "change_dish":
        applied, reply = apply_change_dish(client, plan, dishes, parsed)
        return reply, bool(applied)
    if intent == "swap_dishes":
        applied, reply = apply_swap(plan, parsed)
        return reply, bool(applied)

    return None, False


# =========================================================
# Main loop
# =========================================================

def maybe_quiet_resend(plan, state):
    """
    If there's a pending resend and 15+ min have passed since last edit,
    send updated menu + dashboard.
    """
    if not state.get("pending_resend"):
        return

    last_edit_str = state.get("last_edit_at")
    if not last_edit_str:
        return

    try:
        last_edit = datetime.fromisoformat(last_edit_str)
    except (ValueError, TypeError):
        return

    now = datetime.now()
    quiet_gap = timedelta(minutes=QUIET_RESEND_MINUTES)
    time_since_edit = now - last_edit

    if time_since_edit >= quiet_gap:
        print(f"🕐 {int(time_since_edit.total_seconds()/60)} min since last edit "
              f"(threshold: {QUIET_RESEND_MINUTES} min). Sending quiet update...")
        if do_full_resend(plan):
            state["pending_resend"] = False
            state["last_resend_at"] = now.isoformat()
    else:
        remaining = int((quiet_gap - time_since_edit).total_seconds() / 60)
        print(f"🕐 Pending resend queued. {remaining} min until quiet window.")


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

    client = genai.Client(api_key=GEMINI_KEY)
    processed_ids = []
    any_edit_applied = False

    for update in updates:
        update_id = update.get("update_id")
        message = update.get("message")
        if not message:
            processed_ids.append(update_id)
            continue

        try:
            reply, edit_applied = handle_message(client, plan, dishes, message, state)
        except Exception as e:
            print(f"   Error handling message: {e}")
            reply, edit_applied = None, False

        if reply:
            try:
                send_reply(reply)
                print(f"   Replied: {reply[:120]}")
            except Exception as e:
                print(f"   Failed to send reply: {e}")

        if edit_applied:
            any_edit_applied = True

        processed_ids.append(update_id)

    if processed_ids:
        state["last_update_id"] = max(processed_ids)

    if any_edit_applied:
        state["last_edit_at"] = datetime.now().isoformat()
        state["pending_resend"] = True
        print(f"📝 Edit(s) applied. Quiet resend scheduled for {QUIET_RESEND_MINUTES} min after last edit.")
    else:
        # No edit this run — check if pending resend is due
        maybe_quiet_resend(plan, state)

    save_state(state)
    print(f"State updated. last_update_id = {state.get('last_update_id')}")


if __name__ == "__main__":
    main()