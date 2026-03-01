import os
import time
from flask import Flask, request, render_template
from whatsapp_api_client_python import API
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import openai

app = Flask(__name__)

# --- 1. SECURE CONFIGURATION ---
# These pull from Render's environment variables, falling back to local strings for testing
GREEN_ID = os.environ.get("GREEN_ID", "7103522365")
GREEN_TOKEN = os.environ.get("GREEN_TOKEN", "0760da8b4c294314be900a91cdc1130d773fb00a4579419a9d")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "YOUR_GROQ_API_KEY_HERE")

green_api = API.GreenApi(GREEN_ID, GREEN_TOKEN)

# Hijack OpenAI library to use Groq's free servers
ai_client = openai.OpenAI(
    api_key=GROQ_API_KEY,
    base_url="https://api.groq.com/openai/v1"
)

# Google Sheets Auth
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_name("creds.json", scope)
gc = gspread.authorize(creds)

chat_data = {}


# --- 2. THE VISUAL CATALOG ---
@app.route('/')
def home():
    return "<h1>The Tech Squad Server is Online</h1><p>Append <b>/shop/luxury_hair</b> to the URL to view the catalog.</p>"


@app.route('/shop/<vendor_name>')
def shop(vendor_name):
    try:
        sheet = gc.open("TechSquad").sheet1
        products = sheet.get_all_records()
        formatted_name = vendor_name.replace('_', ' ').title()
        return render_template('catalog.html', vendor=formatted_name, products=products)
    except Exception as e:
        return f"Database Error: {str(e)}", 500


# --- 3. THE WHATSAPP WEBHOOK ---
@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.json
    if not data:
        return "OK", 200

    webhook_type = data.get('typeWebhook')

    if webhook_type == 'outgoingMessageReceived':
        try:
            chat_id = data.get('senderData', {}).get('chatId')
            if chat_id:
                if chat_id not in chat_data:
                    chat_data[chat_id] = {"muted": False, "msg_count": 0, "last_time": 0}
                chat_data[chat_id]["muted"] = True
                print(f"Human Override: Owner replied to {chat_id}.")
        except Exception as e:
            pass
        return "OK", 200

    if webhook_type == 'incomingMessageReceived':
        try:
            sender_data = data.get('senderData', {})
            user_id = sender_data.get('sender')
            message_data = data.get('messageData', {})

            if message_data.get('typeMessage') != 'textMessage':
                return "OK", 200

            text = message_data.get('textMessageData', {}).get('textMessage', '')

            if not user_id or not text:
                return "OK", 200

            if user_id not in chat_data:
                chat_data[user_id] = {"muted": False, "msg_count": 0, "last_time": 0}

            chat = chat_data[user_id]

            if chat["muted"]:
                return "OK", 200

            now = time.time()
            if now - chat["last_time"] < 10:
                chat["msg_count"] += 1
            else:
                chat["msg_count"] = 1
            chat["last_time"] = now

            if chat["msg_count"] > 5:
                chat["muted"] = True
                return "OK", 200

            # --- AI BRAIN ---
            sheet = gc.open("TechSquad").sheet1
            inventory = sheet.get_all_records()

            server_host = request.host_url.rstrip('/')
            catalog_link = f"{server_host}/shop/luxury_hair"

            system_instructions = f"""
            You are Jordan, the primary assistant for The Tech Squad. 
            Your current inventory and prices are: {inventory}. 
            If they ask to see the catalog, provide this link: {catalog_link}
            Be direct, helpful, and professional. 
            If the user is excessively rude, say 'I will leave you be for now' and end the conversation.
            """

            response = ai_client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[
                    {"role": "system", "content": system_instructions},
                    {"role": "user", "content": text}
                ],
                temperature=0.7
            )

            reply = response.choices[0].message.content

            if any(toxic in text.lower() for toxic in ["idiot", "stupid", "useless", "fool"]):
                chat["muted"] = True

            time.sleep(2)
            green_api.sending.sendMessage(user_id, reply)

        except Exception as e:
            print(f"Error: {e}")

    return "OK", 200


if __name__ == '__main__':
    # Render assigns a dynamic port
    port = int(os.environ.get("PORT", 5001))
    app.run(host='0.0.0.0', port=port, debug=False)