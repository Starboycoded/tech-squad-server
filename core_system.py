import os
import time
from flask import Flask, request, render_template
from whatsapp_api_client_python import API
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import openai

app = Flask(__name__)

# --- 1. SECURE CONFIGURATION (MATCHING YOUR DASHBOARD) ---
# We use the exact names from your Render Environment Variables
GREEN_ID = os.environ.get("GREEN_ID")
GREEN_TOKEN = os.environ.get("GREEN_TOKEN")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")

green_api = API.GreenApi(GREEN_ID, GREEN_TOKEN)
ai_client = openai.OpenAI(api_key=GROQ_API_KEY, base_url="https://api.groq.com/openai/v1")

# --- 2. GOOGLE SHEETS AUTH ---
try:
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    # Fixed path for Render's secret file storage
    creds_path = "/etc/secrets/creds.json" if os.path.exists("/etc/secrets/creds.json") else "creds.json"
    creds = ServiceAccountCredentials.from_json_keyfile_name(creds_path, scope)
    gc = gspread.authorize(creds)
    print("SUCCESS: Google Sheets connected.")
except Exception as e:
    print(f"CRITICAL: Sheets connection failed: {e}")
    gc = None


# --- 3. THE WEBHOOK & ORDER LOGIC ---
@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.json
    if not data or data.get('typeWebhook') != 'incomingMessageReceived':
        return "OK", 200

    try:
        sender_data = data.get('senderData', {})
        user_id = sender_data.get('sender')
        text = data.get('messageData', {}).get('textMessageData', {}).get('textMessage', '')

        # AI Order & Validation Logic
        sheet = gc.open("TechSquad").sheet1
        inventory = sheet.get_all_records()

        system_instructions = f"""
        You are Jordan, the Tech Squad assistant. Inventory: {inventory}.

        VALIDATION RULES:
        1. Name: MUST NOT contain numbers.
        2. Address: MUST be a full delivery address (minimum 10 characters).

        If valid, generate a professional INVOICE with:
        - Customer Name
        - Product & Quantity
        - Contact Number
        - Delivery Address
        - Total Amount

        If they put numbers in the name or '2 or 3 letters' for address, politely ask for correct details.
        """

        response = ai_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "system", "content": system_instructions}, {"role": "user", "content": text}]
        )

        reply = response.choices[0].message.content
        green_api.sending.sendMessage(user_id, reply)

    except Exception as e:
        print(f"Error handling webhook: {e}")

    return "OK", 200


# --- 4. CATALOG DISPLAY ---
@app.route('/shop/<vendor_name>')
def shop(vendor_name):
    try:
        sheet = gc.open("TechSquad").sheet1
        products = sheet.get_all_records()
        return render_template('catalog.html', vendor=vendor_name.replace('_', ' ').title(), products=products)
    except Exception as e:
        return f"Database Error: {e}", 500


if __name__ == '__main__':
    # Force the port Render expects (10000)
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)