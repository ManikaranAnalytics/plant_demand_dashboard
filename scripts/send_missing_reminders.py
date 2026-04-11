from datetime import date, timedelta
from pathlib import Path
from email_utils import load_contacts, send_email, get_smtp_config
import os

# Try to import supabase if available
try:
    from supabase import create_client
    SUPABASE_URL = os.getenv("SUPABASE_URL")
    SUPABASE_KEY = os.getenv("SUPABASE_KEY")
    if SUPABASE_URL and SUPABASE_KEY:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    else:
        supabase = None
except ImportError:
    supabase = None

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "plant_data"

def check_submission_supabase(plant_id, target_date):
    if not supabase:
        print(f"Supabase not configured. Cannot check submission for {plant_id}")
        return False
    
    try:
        response = supabase.table("plant_readings") \
            .select("id") \
            .eq("plant_id", plant_id) \
            .eq("date", target_date.strftime("%Y-%m-%d")) \
            .limit(1) \
            .execute()
        return len(response.data) > 0
    except Exception as e:
        print(f"Error checking Supabase for {plant_id}: {e}")
        return False

def main():
    contacts = load_contacts()
    if not contacts:
        print("No contacts found.")
        return

    target_date = date.today() + timedelta(days=2)
    print(f"Target date for reminder check: {target_date.strftime('%d-%m-%Y')} (D+2)")
    
    if not supabase:
        print("ERROR: Supabase is not configured. Cannot verify submissions.")
        return

    missing_plants = []
    
    for plant_id in contacts.keys():
        # Check only Supabase
        if not check_submission_supabase(plant_id, target_date):
            missing_plants.append(plant_id)

    if not missing_plants:
        print("All plants have submitted data for the target date. No reminders sent.")
        return

    print(f"Found {len(missing_plants)} plants with missing data: {', '.join(missing_plants)}")
    
    subject = f"ACTION REQUIRED: Missing Demand Data for {target_date.strftime('%d-%m-%Y')}"
    
    for plant_id in missing_plants:
        email = contacts[plant_id]
        print(f"Sending missing data reminder to {plant_id} ({email})...")
        
        body_html = f"""
        <html>
        <body style="font-family: sans-serif;">
            <h2 style="color: #b91c1c;">Missing Data Alert</h2>
            <p>Dear Plant Owner ({plant_id}),</p>
            <p>Our records show that you have <strong>not yet uploaded</strong> the demand data for <strong>{target_date.strftime('%d %B %Y')}</strong> (D+2).</p>
            <p>Please log in to the dashboard and upload your data immediately using the link below:</p>
            <p><a href="https://plant-demand-dashboard.streamlit.app/" style="background-color: #b91c1c; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px; display: inline-block;">Upload Data Now</a></p>
            <br>
            <p style="font-size: 0.9em; color: #64748b;">If you have recently uploaded the data, please ignore this message.</p>
            <p>Best regards,<br>Operations Team</p>
        </body>
        </html>
        """
        
        success = send_email(email, subject, body_html)
        if success:
            print(f"Successfully sent to {plant_id}")
        else:
            print(f"Failed to send to {plant_id}")

if __name__ == "__main__":
    main()
