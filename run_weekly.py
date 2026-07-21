"""
Day 5: Orchestrator - regenerate plan + grocery + send to Telegram.
Run this once a week (e.g., Sunday morning) for the full pipeline.
"""

import subprocess
import sys
from datetime import date, datetime
import json
import os


def check_plan_freshness():
    """Warn if latest_week_plan.json wasn't generated recently."""
    if not os.path.exists("latest_week_plan.json"):
        return None, "no plan file exists yet"

    with open("latest_week_plan.json", "r", encoding="utf-8") as f:
        plan = json.load(f)

    week_starting = plan.get("week_starting", "")
    try:
        plan_date = datetime.strptime(week_starting, "%Y-%m-%d").date()
    except ValueError:
        return None, "invalid week_starting in file"

    today = date.today()
    days_from_today = (plan_date - today).days
    return days_from_today, week_starting


def run_step(name, command):
    """Run a python script and stop if it fails."""
    print(f"\n{'=' * 60}")
    print(f"▶️  {name}")
    print(f"{'=' * 60}")
    result = subprocess.run(command, shell=True)
    if result.returncode != 0:
        print(f"\n❌ Step '{name}' failed. Aborting pipeline.")
        sys.exit(1)


def main():
    print("🌅 WEEKLY MENU PIPELINE")
    print(f"    Today: {date.today().isoformat()} ({date.today().strftime('%A')})\n")

    days_from_today, week_starting = check_plan_freshness()
    if days_from_today is not None:
        if days_from_today < 0:
            print(f"ℹ️  Existing plan is for {week_starting} — {abs(days_from_today)} days ago. Regenerating.")
        elif days_from_today == 0:
            print(f"ℹ️  Existing plan is for THIS week ({week_starting}). Regenerating anyway.")
        else:
            print(f"ℹ️  Existing plan is for {week_starting} — {days_from_today} days in future. Regenerating.")

    run_step("Step 1/3 — Generating 7-day menu plan", "python plan_week.py")
    run_step("Step 2/3 — Building grocery list", "python make_grocery_list.py")
    run_step("Step 3/3 — Sending to Telegram", "python send_to_telegram.py")

    print("\n" + "=" * 60)
    print("🎉 PIPELINE COMPLETE — check your Telegram group!")
    print("=" * 60)


if __name__ == "__main__":
    main()