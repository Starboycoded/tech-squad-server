import time
import gspread
import openai
from flask import Flask, request
from oauth2client.service_account import ServiceAccountCredentials

app = Flask(__name__)

# --- 1. AUTHENTICATION ---
# Re-using your existing project credentials
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_name("creds.json", scope)
client = gspread.authorize(creds)
openai.api_key = "YOUR_OPENAI_API_KEY"

# Memory to track 'Ghost Mute' and 'Human Takeover'
chat_registry = {}


# --- 2. CORE LOGIC ---
def process_bot_response(user_id, incoming_text, is_from_owner):
    """
    Handles the Ghost Mute hierarchy:
    1. If Owner replies, bot mutes itself for this chat.
    2. If User is toxic or spams, bot mutes itself.
    """
    now = time.time()

    # Initialize chat data if new
    if user_id not in chat_registry:
        chat_registry[user_id] = {
            "muted_by_owner": False,
            "toxic_mute": False,
            "last_time": 0,
            "msg_count": 0
        }

    chat = chat_registry[user_id]

    # HIERARCHY 1: Human Override
    if is_from_owner:
        chat["muted_by_owner"] = True
        print(f"Owner is chatting with {user_id}. Bot is now a Ghost.")
        return None

    # HIERARCHY 2: Safety Checks (Only if not already muted)
    if chat["muted_by_owner"] or chat["toxic_mute"]:
        return None

    # Spam Check: >5 messages in 10 seconds
    if now - chat["last_time"] < 10:
        chat["msg_count"] += 1
    else:
        chat["msg_count"] = 1
    chat["last_time"] = now

    if chat["msg_count"] > 5:
        chat["toxic_mute"] = True
        return None

    # --- 3. THE BRAIN (LLM) ---
    # Fetching your 'TechSquad' inventory
    sheet = client.open("TechSquad").sheet1
    inventory = sheet.get_all_records()

    # Persona instructions using your active ngrok tunnel
    instructions = f"""
    You are 'Jordan' from The Tech Squad. 
    Personality: Human-like, helpful, uses occasional Nigerian nuances. 
    Catalog Link: https://d276-129-222-207-13.ngrok-free.app/shop/luxury_hair

    Inventory: {inventory}

    TASK: Answer questions naturally. If interest is shown, ask for Name, Quantity, and Address.
    GHOST MUTE: If the user is abusive, say 'I'll let you be for now' and then trigger a mute.
    """

    try:
        response = openai.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": instructions},
                {"role": "user", "content": incoming_text}
            ],
            temperature=0.8
        )
        reply = response.choices[0].message.content

        # Internal Toxicity Filter
        if any(bad_word in incoming_text.lower() for bad_word in ["idiot", "stupid", "useless"]):
            chat["toxic_mute"] = True

        return reply
    except Exception as e:
        print(f"Error: {e}")
        return None


# --- 4. THE WEBHOOK ---
@app.route('/webhook', methods=['POST'])
def whatsapp_webhook():
    data = request.json

    # These keys vary by provider (Twilio, Meta, or UltraMsg)
    user_id = data.get('from')
    message_body = data.get('body')
    # This boolean determines if the message came from the phone owner
    sent_by_me = data.get('sent_by_me', False)

    bot_reply = process_bot_response(user_id, message_body, sent_by_me)

    if bot_reply:
        # Simulate typing for a human feel
        time.sleep(2)
        print(f"Sending to {user_id}: {bot_reply}")
        # Insert your API's send function here (e.g., client.messages.create)

    return "OK", 200


if __name__ == '__main__':
    # Running on 5002 to keep the 5001 catalog port free
    app.run(port=5002, debug=True)