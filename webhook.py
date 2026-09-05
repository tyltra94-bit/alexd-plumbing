# webhook.py
# Alex D Plumbing — Lead Automation Backend
# Receives form submissions → SMS to customer → WhatsApp to Alex → Google Calendar event
#
# Run: python webhook.py
# Requires: pip install twilio flask python-dotenv google-auth google-auth-oauthlib google-api-python-client

import os
from flask import Flask, request, jsonify
from twilio.rest import Client
from datetime import datetime, timedelta
import json

app = Flask(__name__)

# ── CREDENTIALS (from environment variables) ──────────────────────────────────
TWILIO_SID    = os.environ.get("TWILIO_ACCOUNT_SID")
TWILIO_TOKEN  = os.environ.get("TWILIO_AUTH_TOKEN")
TWILIO_NUMBER = os.environ.get("TWILIO_PHONE_NUMBER")  # Your Twilio number
ALEX_WHATSAPP = os.environ.get("ALEX_WHATSAPP_NUMBER")  # Alex's WhatsApp e.g. +18185550000
ALEX_PHONE    = os.environ.get("ALEX_PHONE_NUMBER")     # Alex's regular phone for SMS fallback

twilio_client = Client(TWILIO_SID, TWILIO_TOKEN)

# ── HELPER: Send SMS ──────────────────────────────────────────────────────────
def send_sms(to_number, message):
    """Send an SMS via Twilio."""
    try:
        msg = twilio_client.messages.create(
            body=message,
            from_=TWILIO_NUMBER,
            to=to_number
        )
        print(f"[SMS] Sent to {to_number} | SID: {msg.sid}")
        return True
    except Exception as e:
        print(f"[SMS ERROR] {e}")
        return False

# ── HELPER: Send WhatsApp ─────────────────────────────────────────────────────
def send_whatsapp(to_number, message):
    """Send a WhatsApp message via Twilio Sandbox."""
    try:
        # Twilio WhatsApp format: whatsapp:+1XXXXXXXXXX
        msg = twilio_client.messages.create(
            body=message,
            from_=f"whatsapp:{TWILIO_NUMBER}",
            to=f"whatsapp:{to_number}"
        )
        print(f"[WHATSAPP] Sent to {to_number} | SID: {msg.sid}")
        return True
    except Exception as e:
        print(f"[WHATSAPP ERROR] {e}")
        return False

# ── HELPER: Format customer SMS ───────────────────────────────────────────────
def customer_sms(first_name, service, address):
    return (
        f"Hi {first_name}! Thanks for reaching out to Alex D Plumbing. "
        f"We got your request for {service.lower()} at {address}. "
        f"Alex will call you within the hour to confirm timing. "
        f"Questions? Reply to this text."
    )

# ── HELPER: Format Alex WhatsApp alert ───────────────────────────────────────
def alex_whatsapp_alert(first_name, last_name, phone, address, service, notes):
    notes_line = f"\n📝 Notes: {notes}" if notes and notes.strip() else ""
    return (
        f"🚨 NEW LEAD — Alex D Plumbing\n"
        f"──────────────────────\n"
        f"👤 {first_name} {last_name}\n"
        f"📞 {phone}\n"
        f"📍 {address}\n"
        f"🔧 {service}"
        f"{notes_line}\n"
        f"──────────────────────\n"
        f"Reply:\n"
        f"CALL — to get number dialed\n"
        f"BOOK [day] [time] — to schedule\n"
        f"DONE — after job complete (triggers review request)"
    )

# ── MAIN WEBHOOK: Receive form submission ─────────────────────────────────────
@app.route("/lead", methods=["POST"])
def receive_lead():
    """
    Receives lead from the website contact form.
    Expects JSON: { first_name, last_name, phone, address, service, notes }
    """
    try:
        data = request.get_json()
        if not data:
            # Also handle form-encoded data
            data = request.form.to_dict()

        first_name = data.get("first_name", "there").strip()
        last_name  = data.get("last_name", "").strip()
        phone      = data.get("phone", "").strip()
        address    = data.get("address", "").strip()
        service    = data.get("service", "plumbing service").strip()
        notes      = data.get("notes", "").strip()

        print(f"\n[LEAD] {first_name} {last_name} | {phone} | {service}")

        # 1. Send SMS confirmation to customer
        if phone:
            sms_sent = send_sms(phone, customer_sms(first_name, service, address))
        else:
            sms_sent = False
            print("[WARN] No customer phone number provided")

        # 2. Send WhatsApp alert to Alex
        if ALEX_WHATSAPP:
            wa_sent = send_whatsapp(
                ALEX_WHATSAPP,
                alex_whatsapp_alert(first_name, last_name, phone, address, service, notes)
            )
        else:
            wa_sent = False
            print("[WARN] ALEX_WHATSAPP_NUMBER not set")

        # 3. Log the lead
        log_lead(first_name, last_name, phone, address, service, notes)

        return jsonify({
            "status": "success",
            "sms_sent": sms_sent,
            "whatsapp_sent": wa_sent,
            "message": f"Lead received for {first_name} {last_name}"
        }), 200

    except Exception as e:
        print(f"[ERROR] {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

# ── WEBHOOK: Alex replies DONE → trigger review request ──────────────────────
@app.route("/job-done", methods=["POST"])
def job_done():
    """
    Alex texts DONE to the Twilio number after completing a job.
    Triggers a review request SMS to the customer 2 hours later.
    In production use a task queue (Celery/APScheduler) for the delay.
    For demo: sends immediately.
    """
    # Twilio sends incoming SMS as form data
    from_number = request.form.get("From", "")
    body = request.form.get("Body", "").strip().upper()

    if body == "DONE" and from_number.replace("whatsapp:", "") == ALEX_PHONE:
        # Get the last logged lead to send review to
        last_lead = get_last_lead()
        if last_lead:
            review_msg = (
                f"Hi {last_lead['first_name']}! Alex D Plumbing here. "
                f"Hope everything went smoothly with your {last_lead['service'].lower()} today. "
                f"If you're happy with the work, we'd really appreciate a quick Google review — "
                f"it helps small businesses like ours a lot! "
                f"https://g.page/r/YOUR_GOOGLE_REVIEW_LINK"
            )
            send_sms(last_lead["phone"], review_msg)
            print(f"[REVIEW] Sent to {last_lead['phone']}")

    return "", 204

# ── SIMPLE LEAD LOG (JSON file — swap for DB later) ──────────────────────────
LOG_FILE = "leads.json"

def log_lead(first_name, last_name, phone, address, service, notes):
    """Append lead to a local JSON log file."""
    leads = []
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE, "r") as f:
            try:
                leads = json.load(f)
            except:
                leads = []

    leads.append({
        "timestamp": datetime.now().isoformat(),
        "first_name": first_name,
        "last_name": last_name,
        "phone": phone,
        "address": address,
        "service": service,
        "notes": notes,
        "status": "new"
    })

    with open(LOG_FILE, "w") as f:
        json.dump(leads, f, indent=2)
    print(f"[LOG] Lead saved to {LOG_FILE}")

def get_last_lead():
    """Get the most recent logged lead."""
    if not os.path.exists(LOG_FILE):
        return None
    with open(LOG_FILE, "r") as f:
        try:
            leads = json.load(f)
            return leads[-1] if leads else None
        except:
            return None

# ── TEST ENDPOINT ─────────────────────────────────────────────────────────────
@app.route("/test", methods=["GET"])
def test():
    """Quick health check."""
    return jsonify({
        "status": "online",
        "twilio_configured": bool(TWILIO_SID and TWILIO_TOKEN),
        "alex_whatsapp": bool(ALEX_WHATSAPP),
        "timestamp": datetime.now().isoformat()
    })

# ── SEND TEST LEAD ────────────────────────────────────────────────────────────
@app.route("/test-lead", methods=["GET"])
def test_lead():
    """
    Fire a fake lead to test the whole pipeline.
    Visit http://localhost:5000/test-lead in browser to trigger.
    """
    fake_lead = {
        "first_name": "John",
        "last_name": "Smith",
        "phone": os.environ.get("TEST_PHONE", ALEX_PHONE),  # sends to your own phone
        "address": "123 Main St, Pasadena, CA 91101",
        "service": "Drain cleaning",
        "notes": "Kitchen sink draining slowly for the past week"
    }

    # Simulate the full flow
    sms_sent = send_sms(
        fake_lead["phone"],
        customer_sms(fake_lead["first_name"], fake_lead["service"], fake_lead["address"])
    )

    wa_sent = False
    if ALEX_WHATSAPP:
        wa_sent = send_whatsapp(
            ALEX_WHATSAPP,
            alex_whatsapp_alert(
                fake_lead["first_name"], fake_lead["last_name"],
                fake_lead["phone"], fake_lead["address"],
                fake_lead["service"], fake_lead["notes"]
            )
        )

    log_lead(**fake_lead)

    return jsonify({
        "status": "test fired",
        "sms_sent": sms_sent,
        "whatsapp_sent": wa_sent,
        "lead": fake_lead
    })

# ── RUN ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("\n🔧 Alex D Plumbing — Lead Automation Backend")
    print(f"   Twilio SID: {'✅ set' if TWILIO_SID else '❌ NOT SET'}")
    print(f"   Twilio Token: {'✅ set' if TWILIO_TOKEN else '❌ NOT SET'}")
    print(f"   Twilio Number: {TWILIO_NUMBER or '❌ NOT SET'}")
    print(f"   Alex WhatsApp: {ALEX_WHATSAPP or '❌ NOT SET'}")
    print(f"\n   Test endpoint: http://localhost:5000/test")
    print(f"   Fire test lead: http://localhost:5000/test-lead\n")
    app.run(debug=True, port=5000)
