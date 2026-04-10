import os
import json
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
CONTACTS_FILE = BASE_DIR / "scripts" / "contacts.json"

def get_smtp_config():
    return {
        "server": os.getenv("SMTP_SERVER", "smtp.gmail.com"),
        "port": int(os.getenv("SMTP_PORT", "587")),
        "user": os.getenv("SMTP_USER"),
        "password": os.getenv("SMTP_PASSWORD"),
        "from": os.getenv("EMAIL_FROM"),
        "subject_prefix": os.getenv("EMAIL_SUBJECT_PREFIX", "[Plant Reminder]")
    }

def load_contacts():
    if not CONTACTS_FILE.exists():
        return {}
    with open(CONTACTS_FILE, "r") as f:
        return json.load(f)

def send_email(to_email, subject, body_html, body_text=None):
    config = get_smtp_config()
    
    if not config["user"] or not config["password"]:
        print(f"SMTP credentials not configured. Skipping email to {to_email}")
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"{config['subject_prefix']} {subject}"
    msg["From"] = config["from"]
    msg["To"] = to_email

    if body_text:
        msg.attach(MIMEText(body_text, "plain"))
    
    msg.attach(MIMEText(body_html, "html"))

    try:
        if config["port"] == 465:
            server_class = smtplib.SMTP_SSL
        else:
            server_class = smtplib.SMTP

        with server_class(config["server"], config["port"]) as server:
            if config["port"] != 465:
                server.starttls()
            server.login(config["user"], config["password"])
            server.send_message(msg)
        return True
    except Exception as e:
        print(f"Failed to send email to {to_email}: {e}")
        return False
