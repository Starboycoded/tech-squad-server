import os
import time
from flask import Flask, request, render_template
from whatsapp_api_client_python import API
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import openai

app = Flask(__name__)

# --- 1. SECURE CONFIGURATION ---
# Pulling directly from your Render Environment Variables
GREEN_ID = os.environ.get("GREEN_ID", "7103522365")
GREEN_TOKEN = os.environ.get("GREEN_TOKEN", "0760da8b4c294314be900a91cdc1130d773fb00a4579419a9d")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")

green_api = API.GreenApi(GREEN_ID, GREEN_TOKEN)
ai_client = openai.OpenAI(
    api_key=GROQ_API_KEY,
    base_url="https://api.groq.com/openai/v1"
)

# --- 2. GOOGLE SHEETS AUTH WITH RENDER PATH FIX ---
try:
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    # Checks the Render secret path first, then local
    creds_path = "/etc/secrets/creds.json" if os.path.exists("/etc/secrets/creds.json") else "creds.json"
    creds = ServiceAccountCredentials.from_json_keyfile_name(creds_path, scope)
    gc = gspread.authorize(creds)
    print("SUCCESS: Google Sheets connected.")
except Exception as e:
    print(f"CRITICAL: Sheets connection failed: {e}")
    gc = None

chat_data = {}


# --- 3. DATA VALIDATION HELPER ---
def is_valid_input(name, address):
    # Rule: No numbers in Name, Address must be longer than 5 characters
    if any(char.isdigit() for char in name):
        return False, "Names shouldn't contain numbers. Please provide a real name."
    if len(address) < 6:
        return False, "That address is too short. I need a full location for delivery."
    return True, ""


# --- 4. THE WEBHOOK ---
@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.json
    if not data or data.get('typeWebhook') != 'incomingMessageReceived':
        return "OK", 200

    try:
        sender_data = data.get('senderData', {})
        user_id = sender_data.get('sender')
        text = data.get('messageData', {}).get('textMessageData', {}).get('textMessage', '')

        if not user_id or not text:
            return "OK", 200

        # AI Order Logic
        sheet = gc.open("TechSquad").sheet1
        inventory = sheet.get_all_records()

        server_host = request.host_url.rstrip('/')
        catalog_link = f"{server_host}/shop/luxury_hair"

        system_instructions = f"""
        You are Jordan, the Tech Squad assistant. 
        Inventory: {inventory}. Catalog: {catalog_link}.
        When a user orders, you MUST verify:
        1. Name (No numbers allowed)
        2. Product
        3. Quantity
        4. Contact
        5. Full Address (Must be detailed)

        If details are valid, generate a professional Invoice. 
        If they provide nonsense (like '22' for an address), politely ask for the real details.
        """

        response = ai_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "system", "content": system_instructions}, {"role": "user", "content": text}]
        )

        reply = response.choices[0].message.content
        green_api.sending.sendMessage(user_id, reply)

    except Exception as e:
        print(f"Error: {e}")

    return "OK", 200


# --- 5. THE CATALOG ---
@app.route('/shop/<vendor_name>')
def shop(vendor_name):
    try:
        sheet = gc.open("TechSquad").sheet1
        products = sheet.get_all_records()
        return render_template('catalog.html', vendor=vendor_name.replace('_', ' ').title(), products=products)
    except Exception as e:
        return f"Database Error: {e}", 500


if __name__ == '__main__':
    # Force the port Render expects
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)