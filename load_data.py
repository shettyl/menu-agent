"""
Day 3: Read dish catalog, family, and rules from Google Sheets.
Validates the connection and dumps a quick summary.
"""

import os
from dotenv import load_dotenv
import gspread
from google.oauth2.service_account import Credentials

# Load environment variables
load_dotenv()

SHEET_ID = os.getenv("GOOGLE_SHEET_ID")
if not SHEET_ID:
    raise RuntimeError("GOOGLE_SHEET_ID not found in .env file")

SERVICE_ACCOUNT_FILE = "service-account-key.json"

# Scopes = what permissions the service account claims when calling Google APIs.
# These two are read access to Sheets and Drive (Drive needed to find the file).
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def get_sheet():
    """Authenticate with service account and open the menu-agent-db sheet."""
    creds = Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE, scopes=SCOPES
    )
    client = gspread.authorize(creds)
    return client.open_by_key(SHEET_ID)


def load_tab(sheet, tab_name):
    """Load a tab as a list of dictionaries (one dict per row, keyed by header)."""
    try:
        worksheet = sheet.worksheet(tab_name)
        return worksheet.get_all_records()
    except gspread.WorksheetNotFound:
        print(f"  ⚠️  Tab '{tab_name}' not found.")
        return []


def main():
    print("Connecting to Google Sheets...")
    sheet = get_sheet()
    print(f"✅ Connected to: '{sheet.title}'\n")

    # Load each tab
    dishes = load_tab(sheet, "dishes")
    family = load_tab(sheet, "family")
    rules = load_tab(sheet, "rules")
    feedback = load_tab(sheet, "feedback")
    menu_history = load_tab(sheet, "menu_history")

    # Summary
    print("=" * 60)
    print("DATA SUMMARY")
    print("=" * 60)
    print(f"Dishes:        {len(dishes)} entries")
    print(f"Family:        {len(family)} members")
    print(f"Rules:         {len(rules)} rules (active + inactive)")
    print(f"Feedback log:  {len(feedback)} entries")
    print(f"Menu history:  {len(menu_history)} entries")
    print("=" * 60)

    # Quick sanity check on dishes
    if dishes:
        print("\nFirst 3 dishes:")
        for d in dishes[:3]:
            print(f"  - [{d.get('dish_id')}] {d.get('dish_name')} "
                  f"({d.get('meal_slot')}, {d.get('diet_type')})")

    # Quick sanity check on family
    if family:
        print("\nFamily members:")
        for f in family:
            print(f"  - {f.get('person_name')} ({f.get('age')}y, "
                  f"{f.get('diet')}, activity: {f.get('activity_level')})")

    # Quick sanity check on rules
    if rules:
        active_rules = [r for r in rules if str(r.get("active", "")).lower() == "yes"]
        print(f"\nActive rules: {len(active_rules)}")
        for r in active_rules[:3]:
            print(f"  - [{r.get('rule_id')}] {r.get('rule_description')[:80]}...")


if __name__ == "__main__":
    main()