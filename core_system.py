import os
import time
from flask import Flask, request, render_template
from whatsapp_api_client_python import API
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import openai

app = Flask(__name__)

# --- 1. CONFIGURATION ---
GREEN_ID = os.environ.get("GREEN_ID")
GREEN_TOKEN = os.environ.get("GREEN_TOKEN")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")

green_api = API.GreenApi(GREEN_ID, GREEN_TOKEN)

# Stable Groq Connection
ai_client = openai.OpenAI(
    api_key=GROQ_API_KEY,
    base_url="https://api.groq.com/openai/v1"
)

# --- 2. GOOGLE SHEETS AUTH ---
gc = None
try:
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds_path = "/etc/secrets/creds.json" if os.path.exists("/etc/secrets/creds.json") else "creds.json"
    creds = ServiceAccountCredentials.from_json_keyfile_name(creds_path, scope)
    gc = gspread.authorize(creds)
    print("SUCCESS: Google Sheets connected.")
except Exception as e:
    print(f"CRITICAL ERROR (Sheets): {e}")


# --- 3. THE WEBHOOK ---
@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.json
    if not data or data.get('typeWebhook') != 'incomingMessageReceived':
        return "OK", 200

    try:
        sender_data = data.get('senderData', {})
        user_id = sender_data.get('sender')
        text = data.get('messageData', {}).get('textMessageData', {}).get('textMessage', '')

        if not gc: return "OK", 200

        sheet = gc.open("TechSquad").sheet1
        inventory = sheet.get_all_records()

        # Jordan's validation logic
        system_instructions = f"""
        You are Jordan for The Tech Squad. Inventory: {inventory}.
        - Name: No numbers.
        - Address: Must be detailed (min 10 chars).
        If valid, generate an INVOICE. If not, ask for correct details.
        """

        response = ai_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "system", "content": system_instructions}, {"role": "user", "content": text}]
        )

        green_api.sending.sendMessage(user_id, response.choices[0].message.content)
    except Exception as e:
        print(f"Webhook Error: {e}")
    return "OK", 200


@app.route('/shop/<vendor_name>')
def shop(vendor_name):
    try:
        sheet = gc.open("TechSquad").sheet1
        products = sheet.get_all_records()
        return render_template('catalog.html', vendor=vendor_name.replace('_', ' ').title(), products=products)
    except Exception as e:
        return f"Store Error: {e}", 500


@app.route('/')
def health():
    return "System Online", 200


if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)