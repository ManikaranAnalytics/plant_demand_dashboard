@echo off
cd /d "%~dp0"
:: Change to the project root directory
cd ..
:: Run the missing data reminders script
python scripts\send_missing_reminders.py >> scripts\reminder_log.txt 2>&1
echo Reminder attempt finished at %date% %time% >> scripts\reminder_log.txt
