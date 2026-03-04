import os
import time
import uuid
import threading
from flask import Flask, request
from whatsapp_api_client_python import API
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import google.generativeai as genai

app = Flask(__name__)

# --- 1. CONFIGURATION ---
GREEN_ID = os.environ.get("GREEN_ID")
GREEN_TOKEN = os.environ.get("GREEN_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

green_api = API.GreenApi(GREEN_ID, GREEN_TOKEN, "https://7103.api.greenapi.com", "https://7103.media.greenapi.com")

# Native Gemini Configuration (Challenge Optimized)
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-1.5-flash')

chat_sessions = {}
inventory_cache = {"data": None, "last_updated": 0}
processed_messages = {}


# --- 2. DATABASE ---
def connect_sheets():
    try:
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds_path = "/etc/secrets/creds.json" if os.path.exists("/etc/secrets/creds.json") else "creds.json"
        creds = ServiceAccountCredentials.from_json_keyfile_name(creds_path, scope)
        return gspread.authorize(creds)
    except Exception as e:
        print(f"Sheets Connection Error: {e}")
        return None


def get_inventory(sheet_client):
    current_time = time.time()
    if inventory_cache["data"] is None or current_time - inventory_cache["last_updated"] > 600:
        try:
            inventory_cache["data"] = sheet_client.open("TechSquad").sheet1.get_all_records()
            inventory_cache["last_updated"] = current_time
        except Exception as e:
            print(f"Inventory Fetch Error: {e}")
    return inventory_cache["data"]


# --- 3. THE BRAIN ---
def process_conversation(user_id, text):
    try:
        sheet_client = connect_sheets()
        if not sheet_client: return

        inventory = get_inventory(sheet_client)

        # Start or continue native chat session
        if user_id not in chat_sessions:
            chat_sessions[user_id] = model.start_chat(history=[])

        chat = chat_sessions[user_id]

        system_prompt = f"""
        You are Jordan, the welcoming and professional sales assistant for The Tech Squad.
        CATALOG LINK: https://tech-squad-server.onrender.com/shop/tech_squad
        INVENTORY: {inventory}

        RULES:
        1. Greet warmly. Only show the catalog link if asked or at the start.
        2. Keep the conversation natural and helpful.
        3. At checkout, ask for Name, then Address (one by one).
        4. Generate a 'FINAL RECEIPT' and end it with: LOG_ORDER_NOW
        """

        response = chat.send_message(f"{system_prompt}\n\nCustomer: {text}")
        reply = response.text

        # Strip internal markers before sending to WhatsApp
        clean_reply = reply.replace("LOG_ORDER_NOW", "").strip()
        green_api.sending.sendMessage(user_id, clean_reply)

        if "LOG_ORDER_NOW" in reply:
            sales = sheet_client.open("TechSquad").worksheet("Sales")
            order_id = f"TS-{uuid.uuid4().hex[:6].upper()}"
            sales.append_row([order_id, user_id, "Customer", "Order Processed", "Cash on Delivery", "Pending"])
            chat_sessions[user_id] = model.start_chat(history=[])  # Reset session

    except Exception as e:
        print(f"Processing Error: {e}")


# --- 4. WEBHOOK ENGINE ---
@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.json
    if not data or data.get('typeWebhook') != 'incomingMessageReceived':
        return "OK", 200

    msg_id = data.get('idMessage')
    if msg_id in processed_messages:
        return "OK", 200

    processed_messages[msg_id] = time.time()

    sender_data = data.get('senderData', {})
    user_id = sender_data.get('sender')
    msg_data = data.get('messageData', {})
    text = msg_data.get('textMessageData', {}).get('textMessage') or \
           msg_data.get('extendedTextMessageData', {}).get('text')

    if user_id and text:
        threading.Thread(target=process_conversation, args=(user_id, text)).start()

    return "OK", 200


@app.route('/')
def health():
    return "Jordan AI is Online", 200


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))