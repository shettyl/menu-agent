"""
One-time script: backfill menu_history with last week's plan (2026-07-27).
Reads latest_week_plan.json if it contains last week's data, or accepts
a hardcoded fallback for the Sunday-broadcast we restored earlier.

Run once, then delete.
"""

import json
import os
from load_data import get_sheet, load_tab

# Last week's plan (restored from Sunday's Telegram broadcast, 2026-07-27)
LAST_WEEK_PLAN = {
    "week_starting": "2026-07-27",
    "days": [
        {"date": "2026-07-27", "day_of_week": "Monday",
         "breakfast": {"dish_id": "D007"}, "lunch": {"dish_id": "D020"},
         "dinner": {"dish_id": "D020", "protein_booster_dish_id": "D034"}},
        {"date": "2026-07-28", "day_of_week": "Tuesday",
         "breakfast": {"dish_id": "D001"}, "lunch": {"dish_id": "D019"},
         "dinner": {"dish_id": "D019", "protein_booster_dish_id": "D032"}},
        {"date": "2026-07-29", "day_of_week": "Wednesday",
         "breakfast": {"dish_id": "D002"}, "lunch": {"dish_id": "D017"},
         "dinner": {"dish_id": "D017", "protein_booster_dish_id": "D030"}},
        {"date": "2026-07-30", "day_of_week": "Thursday",
         "breakfast": {"dish_id": "D008"}, "lunch": {"dish_id": "D016"},
         "dinner": {"dish_id": "D016", "protein_booster_dish_id": "D029"}},
        {"date": "2026-07-31", "day_of_week": "Friday",
         "breakfast": {"dish_id": "D003"}, "lunch": {"dish_id": "D024"},
         "dinner": {"dish_id": "D024", "protein_booster_dish_id": "D034"}},
        {"date": "2026-08-01", "day_of_week": "Saturday",
         "breakfast": {"dish_id": "D012"}, "lunch": {"dish_id": "D026"},
         "dinner": {"dish_id": "D026", "protein_booster_dish_id": "D031"}},
        {"date": "2026-08-02", "day_of_week": "Sunday",
         "breakfast": {"dish_id": "D009"}, "lunch": {"dish_id": "D027"},
         "dinner": {"dish_id": "D027", "protein_booster_dish_id": "D034"}},
    ]
}


def main():
    print("Backfilling menu_history with last week's plan (2026-07-27)...")
    sheet = get_sheet()
    ws = sheet.worksheet("menu_history")

    # Idempotency check
    existing = load_tab(sheet, "menu_history")
    already = any(str(r.get("week_starting", "")) == "2026-07-27" for r in existing)
    if already:
        print("  ℹ️  Last week already in menu_history. Skipping.")
        return

    rows = []
    for day in LAST_WEEK_PLAN["days"]:
        date_str = day["date"]
        for slot in ("breakfast", "lunch", "dinner"):
            did = day[slot].get("dish_id", "")
            if did:
                rows.append(["2026-07-27", date_str, slot, did, "yes"])
        booster = day["dinner"].get("protein_booster_dish_id", "")
        if booster:
            rows.append(["2026-07-27", date_str, "booster", booster, "yes"])

    ws.append_rows(rows, value_input_option="USER_ENTERED")
    print(f"  ✅ Added {len(rows)} rows.")


if __name__ == "__main__":
    main()