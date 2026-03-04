import os
import time
import uuid
import threading
from flask import Flask, request
from whatsapp_api_client_python import API
import gspread
from oauth2client.service_account import ServiceAccountCredentials


app = Flask(__name__)

# --- 1. CONFIGURATION ---
GREEN_ID = os.environ.get("GREEN_ID")
GREEN_TOKEN = os.environ.get("GREEN_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

green_api = API.GreenApi(GREEN_ID, GREEN_TOKEN, "https://7103.api.greenapi.com", "https://7103.media.greenapi.com")

# FIXED: Removed 'v1beta' or 'v1' from the base URL to let the client handle it
ai_client = openai.OpenAI(
    api_key=GEMINI_API_KEY,
    base_url="https://generativelanguage.googleapis.com/v1beta/"
)

chat_data = {}
gc = None
inventory_cache = {"data": None, "last_updated": 0}
processed_messages = {}


# --- 2. DATABASE ---
def connect_sheets():
    global gc
    if gc is None:
        try:
            scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
            creds_path = "/etc/secrets/creds.json" if os.path.exists("/etc/secrets/creds.json") else "creds.json"
            creds = ServiceAccountCredentials.from_json_keyfile_name(creds_path, scope)
            gc = gspread.authorize(creds)
        except:
            return None
    return gc


def get_inventory(sheet_client):
    current_time = time.time()
    if inventory_cache["data"] is None or current_time - inventory_cache["last_updated"] > 600:
        inventory_cache["data"] = sheet_client.open("TechSquad").sheet1.get_all_records()
        inventory_cache["last_updated"] = current_time
    return inventory_cache["data"]


# --- 3. THE BRAIN ---
def process_conversation(user_id, text):
    try:
        sheet_client = connect_sheets()
        inventory = get_inventory(sheet_client)

        if user_id not in chat_data: chat_data[user_id] = {"history": []}
        session = chat_data[user_id]
        session["history"].append({"role": "user", "content": text})
        session["history"] = session["history"][-10:]

        system_instructions = f"You are Jordan for Tech Squad. Catalog: https://tech-squad-server.onrender.com/shop/tech_squad. Inventory: {inventory}. Rules: Greet warmly. Only show catalog link if asked. Ask for Name then Address at checkout. End receipts with LOG_ORDER_NOW."

        # FIXED: Calling the specific chat endpoint for Gemini
        response = ai_client.chat.completions.create(
            model="gemini-1.5-flash",
            messages=[{"role": "system", "content": system_instructions}] + session["history"]
        )
        reply = response.choices[0].message.content
        session["history"].append({"role": "assistant", "content": reply})

        green_api.sending.sendMessage(user_id, reply.replace("LOG_ORDER_NOW", "").strip())

        if "LOG_ORDER_NOW" in reply:
            sales = sheet_client.open("TechSquad").worksheet("Sales")
            sales.append_row(
                [f"TS-{uuid.uuid4().hex[:6].upper()}", user_id, "Customer", "Checkout", "Cash on Delivery", "Pending"])
            session["history"] = []
    except Exception as e:
        print(f"AI Error: {e}")


# --- 4. WEBHOOK ---
@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.json
    if not data or data.get('typeWebhook') != 'incomingMessageReceived': return "OK", 200

    msg_id = data.get('idMessage')
    if msg_id in processed_messages: return "OK", 200
    processed_messages[msg_id] = time.time()

    sender_data = data.get('senderData', {})
    user_id = sender_data.get('sender')
    msg_data = data.get('messageData', {})
    text = msg_data.get('textMessageData', {}).get('textMessage') or msg_data.get('extendedTextMessageData', {}).get(
        'text')

    if user_id and text:
        threading.Thread(target=process_conversation, args=(user_id, text)).start()
    return "OK", 200


@app.route('/')
def health(): return "Ready", 200


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))