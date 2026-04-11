@echo off
cd /d "%~dp0"
cd ..
echo -------------------------------------------------- >> scripts\reminder_log.txt
echo [3 PM Task] Starting at %date% %time% >> scripts\reminder_log.txt
python scripts\send_daily_reminders.py >> scripts\reminder_log.txt 2>&1
echo [3 PM Task] Finished at %date% %time% >> scripts\reminder_log.txt
