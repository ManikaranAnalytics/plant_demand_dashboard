import time
import datetime
from datetime import timezone, timedelta
import sys
import os

# Ensure we can import the other scripts from the same directory
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import send_daily_reminders
import send_missing_reminders

def get_ist_now():
    """Returns the current date and time in IST (UTC+5:30)."""
    return datetime.datetime.now(timezone(timedelta(hours=5, minutes=30)))

def run_scheduler():
    print("==================================================")
    print("       PLANT REMINDER SCHEDULER STARTED           ")
    print(f"       Current IST Time: {get_ist_now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("==================================================")
    print("Target Windows: 15:00 (3 PM) and 17:00 (5 PM) IST")
    print("Status: Running and waiting...")

    daily_triggered_today = False
    missing_triggered_today = False
    last_date = get_ist_now().date()

    while True:
        now = get_ist_now()
        current_date = now.date()

        # Reset triggers if it's a new day
        if current_date != last_date:
            daily_triggered_today = False
            missing_triggered_today = False
            last_date = current_date
            print(f"\n[{now.strftime('%Y-%m-%d')}] New day started. Resetting triggers.")

        # Trigger 3 PM Task
        if now.hour == 15 and now.minute == 0 and not daily_triggered_today:
            print(f"\n[{now.strftime('%H:%M:%S')}] --- TRIGGERING 3 PM DAILY REMINDERS ---")
            try:
                send_daily_reminders.main()
                daily_triggered_today = True
                print(f"[{now.strftime('%H:%M:%S')}] 3 PM Task completed successfully.")
            except Exception as e:
                print(f"[{now.strftime('%H:%M:%S')}] Error in 3 PM Task: {e}")

        # Trigger 5:15 PM Task (Missing Data)
        if now.hour == 17 and now.minute == 15 and not missing_triggered_today:
            print(f"\n[{now.strftime('%H:%M:%S')}] --- TRIGGERING 5:15 PM MISSING DATA ALERTS ---")
            try:
                send_missing_reminders.main()
                missing_triggered_today = True
                print(f"[{now.strftime('%H:%M:%S')}] 5 PM Task completed successfully.")
            except Exception as e:
                print(f"[{now.strftime('%H:%M:%S')}] Error in 5 PM Task: {e}")

        # Small heartbeat every hour
        if now.minute == 0 and now.second < 30:
            print(f"Heartbeat: System is healthy. Current IST: {now.strftime('%H:%M:%S')}")
            time.sleep(30) # Prevent multiple pulses in the same minute

        time.sleep(30) # Check every 30 seconds

if __name__ == "__main__":
    try:
        run_scheduler()
    except KeyboardInterrupt:
        print("\nScheduler stopped by user.")
    except Exception as e:
        print(f"\nCritical Scheduler Error: {e}")
