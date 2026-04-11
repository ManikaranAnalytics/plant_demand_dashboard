import time
import datetime
from datetime import timezone, timedelta
import sys
import os

print("--- Starting Plant Reminder Service ---")

# Ensure we can import the other scripts from the same directory
try:
    script_dir = os.path.dirname(os.path.abspath(__file__))
    sys.path.append(script_dir)
    print(f"DEBUG: Script directory added to path: {script_dir}")
except Exception as e:
    print(f"DEBUG: Error setting path: {e}")

print("Loading reminder modules...")
try:
    import send_daily_reminders
    import send_missing_reminders
    print("SUCCESS: Modules loaded correctly.")
except Exception as e:
    print(f"CRITICAL ERROR: Failed to load reminder modules: {e}")
    sys.exit(1)

def get_ist_now():
    """Returns the current date and time in IST (UTC+5:30)."""
    return datetime.datetime.now(timezone(timedelta(hours=5, minutes=30)))

def run_scheduler():
    print("\n==================================================")
    print("       PLANT REMINDER SCHEDULER STARTED           ")
    print(f"       Current IST Time: {get_ist_now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("==================================================")
    print("Timing Config:")
    print("  - 15:00 (3:00 PM) : General Reminder")
    print("  - 17:15 (5:15 PM) : Missing Data Alert")
    print("--------------------------------------------------")
    print("Status: Service is ACTIVE and waiting for next window.")
    print("Tip: Keep this window open in the background.")
    print("--------------------------------------------------")

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

        # Regular Heartbeat every 5 minutes (instead of hourly) so user knows it's alive
        if now.minute % 5 == 0 and now.second < 30:
            print(f"Heartbeat: Service is healthy. Time: {now.strftime('%H:%M:%S')} IST")
            time.sleep(30) # Prevent multiple pulses in the same minute

        time.sleep(30) # Check every 30 seconds

if __name__ == "__main__":
    try:
        run_scheduler()
    except KeyboardInterrupt:
        print("\nScheduler stopped by user.")
    except Exception as e:
        print(f"\nCritical Scheduler Error: {e}")
        input("Press Enter to close...") # Keep window open so user can see the error
