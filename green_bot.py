from flask import Flask, request
from whatsapp_api_client_python import API
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import openai
import time

app = Flask(__name__)

# --- 1. CREDENTIALS & CONFIGURATION ---
GREEN_ID = "YOUR_ID_INSTANCE_HERE"
GREEN_TOKEN = "YOUR_API_TOKEN_HERE"
OPENAI_KEY = "YOUR_OPENAI_KEY_HERE"

# Initialize external clients
green_api = API.GreenApi(GREEN_ID, GREEN_TOKEN)
ai_client = openai.OpenAI(api_key=OPENAI_KEY)

# Google Sheets Auth
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_name("creds.json", scope)
gc = gspread.authorize(creds)

# Active Memory for Ghost Mute
chat_data = {}


# --- 2. THE WEBHOOK LOGIC ---
@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.json
    if not data:
        return "OK", 200

    webhook_type = data.get('typeWebhook')

    # HIERARCHY 1: The Human Override (Owner replies from phone)
    if webhook_type == 'outgoingMessageReceived':
        try:
            chat_id = data.get('senderData', {}).get('chatId')
            if chat_id:
                if chat_id not in chat_data:
                    chat_data[chat_id] = {"muted": False, "msg_count": 0, "last_time": 0}

                chat_data[chat_id]["muted"] = True
                print(f"Human Override: Owner replied to {chat_id}. Bot is now a ghost.")
        except Exception as e:
            print(f"Override Error: {e}")
        return "OK", 200

    # HIERARCHY 2: Incoming Customer Messages
    if webhook_type == 'incomingMessageReceived':
        try:
            # Safely parse Green API payload
            sender_data = data.get('senderData', {})
            user_id = sender_data.get('sender')
            message_data = data.get('messageData', {})

            # We only want the AI to process text, not images/audio
            if message_data.get('typeMessage') != 'textMessage':
                return "OK", 200

            text = message_data.get('textMessageData', {}).get('textMessage', '')

            if not user_id or not text:
                return "OK", 200

            # Memory Initialization
            if user_id not in chat_data:
                chat_data[user_id] = {"muted": False, "msg_count": 0, "last_time": 0}

            chat = chat_data[user_id]

            # If the user is muted by the Owner or toxicity rule, ignore them
            if chat["muted"]:
                return "OK", 200

            # HIERARCHY 3: Spam Detection
            now = time.time()
            if now - chat["last_time"] < 10:
                chat["msg_count"] += 1
            else:
                chat["msg_count"] = 1
            chat["last_time"] = now

            if chat["msg_count"] > 5:
                chat["muted"] = True
                print(f"Spam threshold reached for {user_id}. Bot is now a ghost.")
                return "OK", 200

            # --- THE AI BRAIN ---
            sheet = gc.open("TechSquad").sheet1
            inventory = sheet.get_all_records()

            # IMPORTANT: You will manually paste your active 5001 ngrok link here
            catalog_link = "https://[YOUR-ACTIVE-5001-NGROK-LINK].ngrok-free.app/shop/luxury_hair"

            system_instructions = f"""
            You are Jordan, the primary assistant for The Tech Squad. 
            Your current inventory and prices are: {inventory}. 
            If they ask to see the catalog, provide this link: {catalog_link}
            Be direct, helpful, and professional. 
            If the user is excessively rude or abusive, say 'I will leave you be for now' and end the conversation.
            """

            response = ai_client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[
                    {"role": "system", "content": system_instructions},
                    {"role": "user", "content": text}
                ],
                temperature=0.7
            )

            reply = response.choices[0].message.content

            # HIERARCHY 4: Toxicity Mute
            # If the AI was forced to respond to abuse, mute the user after this final message
            if any(toxic_word in text.lower() for toxic_word in ["idiot", "stupid", "useless", "fool"]):
                chat["muted"] = True
                print(f"Toxicity detected from {user_id}. Bot is now a ghost.")

            # Send the response back to WhatsApp
            time.sleep(2)  # Human typing delay
            green_api.sending.sendMessage(user_id, reply)
            print(f"Replied to {user_id}: {reply}")

        except Exception as e:
            print(f"Processing Error: {e}")

    return "OK", 200


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5002, debug=True)