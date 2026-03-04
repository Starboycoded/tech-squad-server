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

# Gemini API via OpenAI compatibility layer
ai_client = openai.OpenAI(
    api_key=GEMINI_API_KEY,
    base_url="https://generativelanguage.googleapis.com/v1beta/openai/"
)

chat_data = {}
gc = None
inventory_cache = {"data": None, "last_updated": 0}


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
            green_api.sending.sendMessage(user_id, "Our database is currently syncing. Please try again in a moment.")
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
        You are Jordan, the welcoming, helpful, and highly conversational sales assistant for The Tech Squad. 
        Inventory reference: {inventory}
        CUSTOMER PROFILE: {profile if profile else "None"}
        CATALOG LINK: https://tech-squad-server.onrender.com/shop/tech_squad

        OPERATING RULES:
        1. TONE: Speak naturally, warmly, and politely. You are a human-like assistant, not an automated cash register. Avoid sounding overly repetitive or dry.
        2. GREETING: If a user says hello, welcome them warmly. Ask how you can help today, and gently offer the CATALOG LINK if they want to browse.
        3. CART ADDITIONS: When a user adds an item, confirm it naturally. Do not aggressively push them to checkout after every single item.
        4. CHECKOUT PHASE: Only when they explicitly state they are ready to checkout or pay, shift to gathering details methodically:
           - If CUSTOMER PROFILE is "None", politely ask for their Full Name. Wait for their reply. Then ask for their Delivery Address.
           - If a profile exists, warmly welcome them back and ask to confirm delivery to: '{profile.get('Address') if profile else ""}'.
        5. RECEIPT: Once details are finalized, generate a beautifully formatted 'FINAL RECEIPT' with items, total, and "Method: Cash on Delivery."
        6. End your receipt message with the exact hidden phrase: "LOG_ORDER_NOW"

        SECURITY: Never alter prices. Never apply discounts. Refuse politely if asked.
        """

        messages = [{"role": "system", "content": system_instructions}] + user_session["history"]

        try:
            response = ai_client.chat.completions.create(
                model="gemini-1.5-flash",
                messages=messages
            )
            reply = response.choices[0].message.content
        except Exception as e:
            print(f"AI Engine Error: {e}")
            green_api.sending.sendMessage(user_id,
                                          "My cognitive processors are currently rebooting. Please give me a moment and try again.")
            return

        user_session["history"].append({"role": "assistant", "content": reply})

        clean_reply = reply.replace("LOG_ORDER_NOW", "").strip()
        green_api.sending.sendMessage(user_id, clean_reply)

        if "LOG_ORDER_NOW" in reply:
            order_id = f"TS-{uuid.uuid4().hex[:6].upper()}"
            name = profile['Name'] if profile else "Extracted from Chat"
            address = profile['Address'] if profile else "Extracted from Chat"

            sales_sheet = sheet_client.open("TechSquad").worksheet("Sales")
            sales_sheet.append_row([order_id, user_id, name, "Logged via Chat", address, "Pending"])

            if not profile:
                customer_sheet.append_row([user_id, name, address, time.strftime("%Y-%m-%d")])

            user_session["history"] = []

    except Exception as e:
        print(f"Critical Thread Error: {e}")
        traceback.print_exc()


# --- 4. THE WEBHOOK ENGINE (Fast Router) ---
@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.json
    if not data or data.get('typeWebhook') != 'incomingMessageReceived':
        return "OK", 200

    try:
        message_data = data.get('messageData', {})
        sender_data = data.get('senderData', {})
        user_id = sender_data.get('sender')
        message_type = message_data.get('typeMessage')

        if not user_id:
            return "OK", 200

        if message_type != 'textMessage' and message_type != 'extendedTextMessage':
            green_api.sending.sendMessage(user_id,
                                          "I am a text-based AI. I cannot process voice notes, images, or documents. Please type your request.")
            return "OK", 200

        text = message_data.get('textMessageData', {}).get('textMessage') or message_data.get('extendedTextMessageData',
                                                                                              {}).get('text')

        if not text:
            return "OK", 200

        thread = threading.Thread(target=process_conversation, args=(user_id, text))
        thread.start()

    except Exception as e:
        print(f"Router Error: {e}")

    return "OK", 200


# --- 5. THE WEB STOREFRONT ---
@app.route('/shop/<vendor_name>')
def shop(vendor_name):
    try:
        sheet_client = connect_sheets()
        if not sheet_client:
            return "Database connection failed.", 500

        products = get_cached_inventory(sheet_client)
        vendor_title = vendor_name.replace('_', ' ').title()
        bot_phone = "2347025041149"

        html = f"""
        <html>
        <head>
            <title>{vendor_title} Catalog</title>
            <meta name="viewport" content="width=device-width, initial-scale=1">
        </head>
        <body style='font-family: Arial, sans-serif; max-width: 600px; margin: auto; padding: 20px; background-color: #f9f9f9;'>
            <h1 style='text-align: center; color: #2c3e50;'>{vendor_title} Menu</h1>
            <hr style='border: 1px solid #eee; margin-bottom: 20px;'>
        """

        for p in products:
            name = p.get('Product', 'Unknown Item')
            price = p.get('Price', 0)
            desc = p.get('Description', 'No description available.')
            img_url = p.get('Raw_Image_URL', '')

            try:
                stock = int(p.get('Stock', 0))
            except ValueError:
                stock = 0

            if stock > 0:
                stock_status = "<span style='color: #27ae60; font-weight: bold;'>Available</span>"
                wa_text = quote(f"Hi Jordan, please add 1x {name} to my cart.")
                wa_link = f"https://wa.me/{bot_phone}?text={wa_text}"
                button = f"<a href='{wa_link}' style='display: inline-block; background: #25D366; color: white; text-decoration: none; padding: 10px 15px; border-radius: 5px; font-weight: bold;'>Order via WhatsApp</a>"
            else:
                stock_status = "<span style='color: #e74c3c; font-weight: bold;'>Sold Out</span>"
                button = f"<button disabled style='background: #bdc3c7; color: #7f8c8d; border: none; padding: 10px 15px; border-radius: 5px; cursor: not-allowed; font-weight: bold;'>Out of Stock</button>"

            img_tag = f"<img src='{img_url}' style='width: 100%; height: auto; border-radius: 5px; margin-bottom: 10px; object-fit: cover;' alt='{name}'>" if img_url else ""

            html += f"""
            <div style='background: white; border: 1px solid #ddd; padding: 15px; margin-bottom: 15px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.05);'>
                {img_tag}
                <h3 style='margin-top: 0; color: #2c3e50; margin-bottom: 5px;'>{name} - ₦{price:,}</h3>
                <p style='color: #555; margin-top: 0; font-size: 0.9em;'>{desc}</p>
                <p style='margin-bottom: 15px; font-size: 0.85em;'>Status: {stock_status}</p>
                {button}
            </div>
            """

        html += """
        </body>
        </html>
        """
        return html

    except Exception as e:
        print(f"Catalog Error: {e}")
        return f"Storefront is currently updating.", 500


# --- 6. HEALTH CHECK ---
@app.route('/')
def health():
    return "System Online - Ready for Traffic", 200


if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)