import os
import time
import uuid
import traceback
import threading
from urllib.parse import quote
from flask import Flask, request
from whatsapp_api_client_python import API
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import openai

app = Flask(__name__)

# --- 1. CONFIGURATION ---
GREEN_ID = os.environ.get("GREEN_ID")
GREEN_TOKEN = os.environ.get("GREEN_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

green_api = API.GreenApi(
    GREEN_ID,
    GREEN_TOKEN,
    "https://7103.api.greenapi.com",
    "https://7103.media.greenapi.com"
)

# FIXED: Using the production v1 endpoint for OpenAI compatibility
ai_client = openai.OpenAI(
    api_key=GEMINI_API_KEY,
    base_url="https://generativelanguage.googleapis.com/v1/openai/"
)

chat_data = {}
gc = None
inventory_cache = {"data": None, "last_updated": 0}
processed_messages = {}  # For deduplication


# --- 2. DATABASE & CACHE ---
def connect_sheets():
    global gc
    if gc is None:
        try:
            scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
            creds_path = "/etc/secrets/creds.json" if os.path.exists("/etc/secrets/creds.json") else "creds.json"
            creds = ServiceAccountCredentials.from_json_keyfile_name(creds_path, scope)
            gc = gspread.authorize(creds)
        except Exception as e:
            print(f"Sheets failed: {e}")
    return gc


def get_cached_inventory(sheet_client):
    current_time = time.time()
    if inventory_cache["data"] is None or current_time - inventory_cache["last_updated"] > 600:
        inventory_sheet = sheet_client.open("TechSquad").sheet1
        inventory_cache["data"] = inventory_sheet.get_all_records()
        inventory_cache["last_updated"] = current_time
    return inventory_cache["data"]


# --- 3. THE BRAIN (Background Thread) ---
def process_conversation(user_id, text):
    try:
        sheet_client = connect_sheets()
        if not sheet_client:
            green_api.sending.sendMessage(user_id, "Our database is currently syncing. Please try again.")
            return

        customer_sheet = sheet_client.open("TechSquad").worksheet("Customers")
        customers = customer_sheet.get_all_records()
        profile = next((r for r in customers if str(r['Phone']) == str(user_id)), None)

        if user_id not in chat_data:
            chat_data[user_id] = {"history": []}
        user_session = chat_data[user_id]

        user_session["history"].append({"role": "user", "content": text})
        user_session["history"] = user_session["history"][-15:]

        inventory = get_cached_inventory(sheet_client)

        system_instructions = f"""
        You are Jordan, the welcoming and helpful sales assistant for The Tech Squad. 
        Inventory: {inventory}
        CUSTOMER PROFILE: {profile if profile else "None"}
        CATALOG LINK: https://tech-squad-server.onrender.com/shop/tech_squad

        OPERATING RULES:
        1. GREETING: If a user says hello, welcome them and ask if they'd like to browse the catalog.
        2. THE LINK: Provide the CATALOG LINK only if they say yes or ask for products.
        3. CART: Confirm additions naturally.
        4. CHECKOUT: Ask for Full Name, then Address (if new). If existing, confirm their saved address.
        5. RECEIPT: Generate 'FINAL RECEIPT' and end with "LOG_ORDER_NOW".
        """

        messages = [{"role": "system", "content": system_instructions}] + user_session["history"]

        try:
            # FIXED: Explicitly calling the model path required by Gemini
            response = ai_client.chat.completions.create(
                model="models/gemini-1.5-flash",
                messages=messages
            )
            reply = response.choices[0].message.content
        except Exception as e:
            print(f"AI Engine Error: {e}")
            green_api.sending.sendMessage(user_id, "System is busy. Please try again in 30 seconds.")
            return

        user_session["history"].append({"role": "assistant", "content": reply})
        clean_reply = reply.replace("LOG_ORDER_NOW", "").strip()
        green_api.sending.sendMessage(user_id, clean_reply)

        if "LOG_ORDER_NOW" in reply:
            order_id = f"TS-{uuid.uuid4().hex[:6].upper()}"
            name = profile['Name'] if profile else "New Customer"
            sales_sheet = sheet_client.open("TechSquad").worksheet("Sales")
            sales_sheet.append_row([order_id, user_id, name, "Logged", "See Chat", "Pending"])
            user_session["history"] = []

    except Exception as e:
        print(f"Critical Error: {e}")


# --- 4. THE WEBHOOK ENGINE ---
@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.json
    if not data or data.get('typeWebhook') != 'incomingMessageReceived':
        return "OK", 200

    try:
        message_id = data.get('idMessage')
        sender_data = data.get('senderData', {})
        user_id = sender_data.get('sender')

        # DEDUPLICATION
        current_time = time.time()
        if message_id in processed_messages:
            return "OK", 200
        processed_messages[message_id] = current_time

        message_data = data.get('messageData', {})
        text = message_data.get('textMessageData', {}).get('textMessage') or \
               message_data.get('extendedTextMessageData', {}).get('text')

        if user_id and text:
            thread = threading.Thread(target=process_conversation, args=(user_id, text))
            thread.start()

    except Exception as e:
        print(f"Webhook Error: {e}")

    return "OK", 200


# --- 5. THE WEB STOREFRONT ---
@app.route('/shop/<vendor_name>')
def shop(vendor_name):
    try:
        sheet_client = connect_sheets()
        products = get_cached_inventory(sheet_client)
        html = f"<html><body><h1>{vendor_name.title()} Catalog</h1>"
        for p in products:
            html += f"<div><h3>{p['Product']} - ₦{p['Price']}</h3><p>{p['Description']}</p></div>"
        return html + "</body></html>"
    except:
        return "Error loading shop.", 500


@app.route('/')
def health():
    return "Online", 200


if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)