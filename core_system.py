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

# Configure Gemini with the EXACT model ID to fix the 404
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-1.5-flash')

chat_sessions = {}
processed_messages = {}


# --- 2. DATABASE ---
def connect_sheets():
    try:
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        # Standard path for Render secrets
        creds_path = "/etc/secrets/creds.json" if os.path.exists("/etc/secrets/creds.json") else "creds.json"
        creds = ServiceAccountCredentials.from_json_keyfile_name(creds_path, scope)
        return gspread.authorize(creds)
    except:
        return None


# --- 3. THE BRAIN ---
def process_conversation(user_id, text):
    try:
        sheet_client = connect_sheets()
        if not sheet_client: return

        # Simple inventory fetch
        inventory = sheet_client.open("TechSquad").sheet1.get_all_records()

        if user_id not in chat_sessions:
            chat_sessions[user_id] = model.start_chat(history=[])

        chat = chat_sessions[user_id]

        system_instructions = f"Identity: Jordan from Tech Squad. Catalog: https://tech-squad-server.onrender.com/shop/tech_squad. Inventory: {inventory}. Rule: Greet warmly. End receipts with LOG_ORDER_NOW."

        # The fix: Send instruction and user text together
        response = chat.send_message(f"Instruction: {system_instructions}\n\nUser: {text}")
        reply = response.text

        green_api.sending.sendMessage(user_id, reply.replace("LOG_ORDER_NOW", "").strip())

        if "LOG_ORDER_NOW" in reply:
            sales = sheet_client.open("TechSquad").worksheet("Sales")
            sales.append_row(
                [f"TS-{uuid.uuid4().hex[:6].upper()}", user_id, "WhatsApp User", "New Order", "COD", "Pending"])
            chat_sessions[user_id] = model.start_chat(history=[])
    except Exception as e:
        print(f"Jordan Error: {e}")


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
def health(): return "Jordan Online", 200


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))