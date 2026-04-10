from email_utils import load_contacts, send_email

def main():
    contacts = load_contacts()
    if not contacts:
        print("No contacts found in plant_data/contacts.json")
        return

    subject = "Daily Demand Data Submission Reminder"
    
    for plant_id, email in contacts.items():
        print(f"Sending general reminder to {plant_id} ({email})...")
        
        body_html = f"""
        <html>
        <body style="font-family: sans-serif;">
            <h2 style="color: #1e3a5f;">Daily Data Reminder</h2>
            <p>Dear Plant Owner ({plant_id}),</p>
            <p>This is a friendly reminder to upload your daily demand data for the upcoming days.</p>
            <p>Please log in to the <strong>Plant Demand Dashboard</strong> to submit your daily data template:</p>
            <p><a href="https://plant-demand-dashboard.streamlit.app/" style="background-color: #1e3a5f; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px; display: inline-block;">Open Dashboard</a></p>
            <br>
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
