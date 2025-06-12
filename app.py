from flask import Flask, request, jsonify
import requests

app = Flask(__name__)

GOOGLE_SHEETS_WEBHOOK_URL = "https://script.google.com/macros/s/AKfycbx-SO8QlqurzYSDtaCu0NtofAXzXyi0aZ6TtcnJhp_5tDtCf3iEvJ4Z7ikTcP8kqzg8/exec"

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.json
    print(f"📥 Received webhook: {data}")

    payload = {
        "ticker": data.get("ticker"),
        "timeframe": data.get("timeframe", "1H"),
        "strategy": data.get("version", "Auto"),
        "type": data.get("alert", "Unknown"),
        "price": data.get("price", "")
    }

    try:
        response = requests.post(GOOGLE_SHEETS_WEBHOOK_URL, json=payload)
        response.raise_for_status()
        print("✅ Sent to Google Sheets successfully.")
        return jsonify({"status": "sent to Google Sheets"}), 200
    except Exception as e:
        print(f"❌ Error sending to Google Sheets: {e}")
        return jsonify({"status": "error", "details": str(e)}), 500

if __name__ == '__main__':
    print("🚀 Flask server is starting at http://localhost:5000")
    app.run(host='0.0.0.0', port=5000)
