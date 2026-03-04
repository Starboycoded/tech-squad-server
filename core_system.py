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

# Official Gemini Configuration - Optimized for the 1.5 Flash Model
genai.configure(api_key=GEMINI_API_KEY)
# FIXED: Using the exact model string the SDK expects to resolve the 404
model = genai.GenerativeModel('gemini-1.5-flash-latest')

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
    except:
        return None


def get_inventory(sheet_client):
    current_time = time.time()
    if inventory_cache["data"] is None or current_time - inventory_cache["last_updated"] > 600:
        try:
            inventory_cache["data"] = sheet_client.open("TechSquad").sheet1.get_all_records()
            inventory_cache["last_updated"] = current_time
        except:
            pass
    return inventory_cache["data"]


# --- 3. THE BRAIN ---
def process_conversation(user_id, text):
    try:
        sheet_client = connect_sheets()
        if not sheet_client: return
        inventory = get_inventory(sheet_client)

        if user_id not in chat_sessions:
            chat_sessions[user_id] = model.start_chat(history=[])

        chat = chat_sessions[user_id]

        system_prompt = f"You are Jordan for Tech Squad. Catalog: https://tech-squad-server.onrender.com/shop/tech_squad. Inventory: {inventory}. Rules: Greet warmly. Show catalog link if relevant. End receipts with LOG_ORDER_NOW."

        response = chat.send_message(f"{system_prompt}\n\nCustomer: {text}")
        reply = response.text

        green_api.sending.sendMessage(user_id, reply.replace("LOG_ORDER_NOW", "").strip())

        if "LOG_ORDER_NOW" in reply:
            sales = sheet_client.open("TechSquad").worksheet("Sales")
            sales.append_row(
                [f"TS-{uuid.uuid4().hex[:6].upper()}", user_id, "WhatsApp Client", "New Order", "COD", "Pending"])
            chat_sessions[user_id] = model.start_chat(history=[])
    except Exception as e:
        print(f"Jordan AI Error: {e}")


# --- 4. WEBHOOK ENGINE ---
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
def health(): return "System Online", 200


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))