import gspread
from oauth2client.service_account import ServiceAccountCredentials
from flask import Flask, render_template

app = Flask(__name__)

# Google Sheets Authentication
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_name("creds.json", scope)
gc = gspread.authorize(creds)


@app.route('/')
def home():
    return "<h1>The Tech Squad Server is Online</h1><p>Append <b>/shop/luxury_hair</b> to the URL to view the catalog.</p>"


@app.route('/shop/<vendor_name>')
def shop(vendor_name):
    try:
        # Opens the exact sheet named 'TechSquad'
        sheet = gc.open("TechSquad").sheet1
        products = sheet.get_all_records()

        formatted_name = vendor_name.replace('_', ' ').title()
        return render_template('catalog.html', vendor=formatted_name, products=products)

    except Exception as e:
        return f"Database Error: {str(e)}", 500


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5001, debug=True)