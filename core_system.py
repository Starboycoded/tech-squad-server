import os
import time
import uuid
import traceback
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
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")

# Explicit positional arguments for your specific 7103 server
green_api = API.GreenApi(
    GREEN_ID,
    GREEN_TOKEN,
    "https://7103.api.greenapi.com",
    "https://7103.media.greenapi.com"
)

ai_client = openai.OpenAI(api_key=GROQ_API_KEY, base_url="https://api.groq.com/openai/v1")

chat_data = {}
gc = None

# --- 2. LAZY LOADER FOR GOOGLE SHEETS ---
def connect_sheets():
    global gc
    if gc is None:
        try:
            scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
            creds_path = "/etc/secrets/creds.json" if os.path.exists("/etc/secrets/creds.json") else "creds.json"
            creds = ServiceAccountCredentials.from_json_keyfile_name(creds_path, scope)
            gc = gspread.authorize(creds)
            print("SUCCESS: Google Sheets connected.")
        except Exception as e:
            print(f"CRITICAL: Sheets failed: {e}")
    return gc

# --- 3. HEALTH CHECK ---
@app.route('/')
def health():
    return "System Online", 200

# --- 4. THE WEBHOOK ENGINE ---
@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.json
    if not data or data.get('typeWebhook') != 'incomingMessageReceived':
        return "OK", 200

    try:
        sender_data = data.get('senderData', {})
        user_id = sender_data.get('sender')
        text = data.get('messageData', {}).get('textMessageData', {}).get('textMessage', '')

        sheet_client = connect_sheets()
        if not sheet_client:
            return "OK", 200

        # Memory Check
        customer_sheet = sheet_client.open("TechSquad").worksheet("Customers")
        customers = customer_sheet.get_all_records()
        profile = next((r for r in customers if str(r['Phone']) == str(user_id)), None)

        # Session Init
        if user_id not in chat_data:
            chat_data[user_id] = {"cart": []}
        user_session = chat_data[user_id]

        # Inventory Fetch
        inventory_sheet = sheet_client.open("TechSquad").sheet1
        inventory = inventory_sheet.get_all_records()

        # UPDATED: Strict instructions to prevent premature data collection
        system_instructions = f"""
                You are Jordan, the efficient assistant for The Tech Squad. 
                Inventory reference (for price checking only): {inventory}. 
                CUSTOMER PROFILE: {profile if profile else "None"}
                CATALOG LINK: https://tech-squad-server.onrender.com/shop/tech_squad

                STRICT RULES:
                1. GREETING/BROWSING: When a user says hello or asks what you sell, DO NOT list the inventory in the chat. Welcome them and give them the CATALOG LINK to view products themselves. 
                2. CART: When they tell you what they chose from the link, confirm the items and the total price. Do not ask for their address yet.
                3. CHECKOUT: ONLY when the user explicitly says they are ready to checkout/pay, move to this phase.
                   - If CUSTOMER PROFILE is "None", ask for their Name (no numbers) and a detailed Delivery Address.
                   - If a profile exists, ask if they want delivery to their saved address: '{profile.get('Address') if profile else ""}'.
                4. RECEIPT: Once details are finalized, generate a beautifully formatted 'FINAL RECEIPT' listing their items and total. Add: 'For this test phase, we are using Cash on Delivery.'
                5. End your receipt message with the exact hidden phrase: "LOG_ORDER_NOW"
                """

        response = ai_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "system", "content": system_instructions}, {"role": "user", "content": text}]
        )

        reply = response.choices[0].message.content

        # Strip the hidden phrase before sending it to the user
        clean_reply = reply.replace("LOG_ORDER_NOW", "").strip()
        green_api.sending.sendMessage(user_id, clean_reply)

        # If the AI decided it was time to print a receipt, log the event
        if "LOG_ORDER_NOW" in reply:
            order_id = f"TS-{uuid.uuid4().hex[:6].upper()}"
            name = profile['Name'] if profile else "Extracted from Chat"
            address = profile['Address'] if profile else "Extracted from Chat"

            # Log to Sales Tab
            sales_sheet = sheet_client.open("TechSquad").worksheet("Sales")
            sales_sheet.append_row([order_id, user_id, name, "Logged", address, "Pending"])

            # Save new user profile if they didn't exist
            if not profile:
                customer_sheet.append_row([user_id, name, address, time.strftime("%Y-%m-%d")])

            user_session["cart"] = []  # Clear cart

    except Exception as e:
        print(f"Error: {e}")
        traceback.print_exc()
        if hasattr(e, 'args') and len(e.args) > 0 and hasattr(e.args[0], 'text'):
            print(f"API Response Details: {e.args[0].text}")

    return "OK", 200

# --- 5. THE WEB STOREFRONT ---
@app.route('/shop/<vendor_name>')
def shop(vendor_name):
    try:
        sheet_client = connect_sheets()
        if not sheet_client:
            return "Database connection failed. Please try again later.", 500

        inventory_sheet = sheet_client.open("TechSquad").sheet1
        products = inventory_sheet.get_all_records()

        vendor_title = vendor_name.replace('_', ' ').title()
        bot_phone = "2347025041149"  # Your Green API Bot Number

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

            # Safely handle the stock value
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

            html += f"""
            <div style='background: white; border: 1px solid #ddd; padding: 15px; margin-bottom: 15px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.05);'>
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
        return f"Storefront is currently updating. Please check back in a moment.", 500

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)