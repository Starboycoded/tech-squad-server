import os
import time
import uuid
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
ai_client = openai.OpenAI(api_key=GROQ_API_KEY, base_url="https://api.groq.com/openai/v1")

chat_data = {}  # Temporary session memory

# --- 2. GOOGLE SHEETS CONNECTION ---
try:
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds_path = "/etc/secrets/creds.json" if os.path.exists("/etc/secrets/creds.json") else "creds.json"
    creds = ServiceAccountCredentials.from_json_keyfile_name(creds_path, scope)
    gc = gspread.authorize(creds)
    print("SUCCESS: Google Sheets connected.")
except Exception as e:
    print(f"CRITICAL: Sheets failed: {e}")
    gc = None


# --- 3. THE WEBHOOK ENGINE ---
@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.json
    if not data or data.get('typeWebhook') != 'incomingMessageReceived':
        return "OK", 200

    try:
        sender_data = data.get('senderData', {})
        user_id = sender_data.get('sender')
        text = data.get('messageData', {}).get('textMessageData', {}).get('textMessage', '')

        # 1. Profile Memory Check
        customer_sheet = gc.open("TechSquad").worksheet("Customers")
        customers = customer_sheet.get_all_records()
        profile = next((r for r in customers if str(r['Phone']) == str(user_id)), None)

        if user_id not in chat_data:
            chat_data[user_id] = {"cart": []}
        user_session = chat_data[user_id]

        # 2. AI Instructions
        sheet = gc.open("TechSquad").sheet1
        inventory = sheet.get_all_records()

        system_instructions = f"""
        You are Jordan. Inventory: {inventory}. 
        CUSTOMER PROFILE: {profile if profile else "New Customer"}

        RULES:
        - Add items to cart as requested.
        - If profile exists, confirm the address: '{profile.get('Address') if profile else ""}'.
        - To finish, generate a 'FINAL RECEIPT'. 
        - For payment, tell them: 'For this test phase, we are using Cash on Delivery. Simply confirm to place the order.'
        """

        response = ai_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "system", "content": system_instructions}, {"role": "user", "content": text}]
        )

        reply = response.choices[0].message.content

        # 3. Structured Receipt Logic
        if "RECEIPT" in reply.upper() and user_session["cart"]:
            order_id = f"TS-{uuid.uuid4().hex[:6].upper()}"
            subtotal = sum(item['price'] * item['qty'] for item in user_session["cart"])
            items_list = "\n".join(
                [f"• {i['name']} x{i['qty']} - ₦{i['price'] * i['qty']:,}" for i in user_session["cart"]])

            # Using real or extracted details
            name = profile['Name'] if profile else "New Client"
            address = profile['Address'] if profile else "Address Provided in Chat"

            receipt = f"""
*TECH SQUAD OFFICIAL RECEIPT*
------------------------------------
*Order ID:* {order_id}
*Customer:* {name}
------------------------------------
*ITEMS:*
{items_list}
------------------------------------
*TOTAL:* ₦{subtotal:,}
*PAYMENT:* Cash on Delivery (Test Mode)
------------------------------------
*DELIVERY:* {address}
------------------------------------
_Your order is logged. A human will confirm shortly._
"""
            green_api.sending.sendMessage(user_id, receipt)

            # 4. Log to Sales Tab
            sales_sheet = gc.open("TechSquad").worksheet("Sales")
            sales_sheet.append_row([order_id, user_id, name, subtotal, address, "Pending"])
            user_session["cart"] = []  # Clear cart
        else:
            green_api.sending.sendMessage(user_id, reply)

    except Exception as e:
        print(f"Error: {e}")
    return "OK", 200


if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)