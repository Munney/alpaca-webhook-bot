from flask import Flask, request, jsonify
import requests

app = Flask(__name__)

# Replace with your actual Google Apps Script Webhook URL
GOOGLE_SHEETS_WEBHOOK_URL = "https://script.google.com/macros/s/AKfycbx-SO8QlqurzYSDtaCu0NtofAXzXyi0aZ6TtcnJhp_5tDtCf3iEvJ4Z7ikTcP8kqzg8/exec"

@app.route('/', methods=['GET'])
def root():
    return "✅ Alpaca Webhook Bot is live!", 200

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        # Log everything received
        raw_body = request.data.decode('utf-8')
        print(f"📦 Raw body:\n{raw_body}")
        print(f"📨 Headers:\n{dict(request.headers)}")

        # Attempt to parse JSON
        data = request.get_json(force=True, silent=True)
        print(f"📋 Parsed JSON:\n{data}")

        if not data:
            raise ValueError("Empty or invalid JSON payload")

        # Validate required fields
        required = ["ticker", "timeframe", "version", "alert", "price"]
        for field in required:
            if field not in data:
                error_msg = f"Missing field: {field}"
                print(f"❌ {error_msg}")
                return jsonify({"error": error_msg}), 400

        # Prepare and send to Google Sheets
        payload = {
            "ticker": data["ticker"],
            "timeframe": data["timeframe"],
            "strategy": data["version"],
            "type": data["alert"],
            "price": str(data["price"])
        }

        response = requests.post(GOOGLE_SHEETS_WEBHOOK_URL, json=payload)
        response.raise_for_status()
        print("✅ Sent to Google Sheets successfully.")
        return jsonify({"status": "sent to Google Sheets"}), 200

    except Exception as e:
        print(f"❌ Exception:\n{e}")
        return jsonify({"error": "Invalid signal", "details": str(e)}), 400


if __name__ == '__main__':
    print("🚀 Flask server is starting at http://localhost:5000")
    app.run(host='0.0.0.0', port=5000)
