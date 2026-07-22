"""
Day 8: Evening feedback prompt.

Reads today's meals from latest_week_plan.json and sends a friendly
rating request to the family Telegram group.

Runs daily at 9 PM IST via GitHub Actions.
"""

import os
import json
import urllib.request
import urllib.parse
from datetime import date, datetime
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")

PLAN_FILE = "latest_week_plan.json"


def send_message(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": CHAT_ID,
        "text": text,
    }).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    with urllib.request.urlopen(req) as resp:
        result = json.loads(resp.read())
    if not result.get("ok"):
        raise RuntimeError(f"Telegram error: {result}")


def load_plan():
    if not os.path.exists(PLAN_FILE):
        return None
    with open(PLAN_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def find_todays_meals(plan):
    """Return today's day dict from the plan, or None if not found."""
    today = date.today().isoformat()
    for day in plan["days"]:
        if day.get("date") == today:
            return day
    return None


def build_prompt_message(day):
    """Build the evening rating message. Includes dish names for context."""
    bf = day["breakfast"].get("dish_name", "?")
    lunch = day["lunch"].get("dish_name", "?")
    dinner = day["dinner"].get("dish_name", "?")

    return (
        f"🌙 How was today's food?\n"
        f"({day['day_of_week']}, {day['date']})\n\n"
        f"🍳 Breakfast: {bf}\n"
        f"🍛 Lunch:     {lunch}\n"
        f"🍽️ Dinner:    {dinner}\n\n"
        f"Rate 1-5 for each (breakfast, lunch, dinner).\n"
        f"Example replies:\n"
        f"  • '4,3,5'   ← breakfast 4, lunch 3, dinner 5\n"
        f"  • 'rating: 5 4 5'\n"
        f"  • 'skip'    ← if you don't want to rate today\n"
    )


def main():
    print(f"🌙 Evening feedback prompt at {datetime.now().isoformat()}")

    if not BOT_TOKEN or not CHAT_ID:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID env var.")

    plan = load_plan()
    if not plan:
        print("No plan file found. Skipping.")
        return

    day = find_todays_meals(plan)
    if not day:
        print(f"No meals found for today ({date.today().isoformat()}). Skipping.")
        return

    message = build_prompt_message(day)
    print(f"Sending feedback prompt...\n{message}")

    send_message(message)
    print("✅ Sent.")


if __name__ == "__main__":
    main()