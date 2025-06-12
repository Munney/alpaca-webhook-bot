from flask import Flask, request, jsonify
import requests
import os

app = Flask(__name__)

# === Google Sheets Webhook URL ===
GOOGLE_SHEETS_WEBHOOK_URL = "https://script.google.com/macros/s/AKfycbx-SO8QlqurzYSDtaCu0NtofAXzXyi0aZ6TtcnJhp_5tDtCf3iEvJ4Z7ikTcP8kqzg8/exec"

# === Alpaca API Keys (Paper or Live) ===
ALPACA_API_KEY = "PKDYG000ARPL9C625NQD"
ALPACA_SECRET_KEY = "Nn9uKqzFbFfqaxXgyXkCLrlETfF1DMN6STNvX4jG"
ALPACA_BASE_URL = "https://paper-api.alpaca.markets/v2"  # Change to live URL if using real money

@app.route('/', methods=['GET'])
def root():
    return "✅ Alpaca Webhook Bot is live!", 200

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        raw_body = request.data.decode('utf-8')
        print(f"📦 Raw body:\n{raw_body}")
        data = request.get_json(force=True, silent=True)
        print(f"📋 Parsed JSON:\n{data}")

        if not data:
            raise ValueError("Empty or invalid JSON")

        # === Validate required fields ===
        required = ["ticker", "timeframe", "version", "alert", "price"]
        for field in required:
            if field not in data:
                return jsonify({"error": f"Missing field: {field}"}), 400

        ticker = data["ticker"]
        action = data["alert"].lower()
        price = float(data["price"])

        # === Send to Google Sheets ===
        gs_payload = {
            "ticker": ticker,
            "timeframe": data["timeframe"],
            "strategy": data["version"],
            "type": data["alert"],
            "price": str(price)
        }
        requests.post(GOOGLE_SHEETS_WEBHOOK_URL, json=gs_payload)
        print("✅ Sent to Google Sheets.")

        # === Prepare Alpaca Order ===
        if action in ["long entry", "short entry"]:
            side = "buy" if action == "long entry" else "sell"
            order_payload = {
                "symbol": ticker,
                "qty": 1,
                "side": side,
                "type": "market",
                "time_in_force": "gtc"
            }
            alpaca_response = requests.post(
                f"{ALPACA_BASE_URL}/orders",
                json=order_payload,
                headers={
                    "APCA-API-KEY-ID": "PKDYG000ARPL9C625NQD",
                    "APCA-API-SECRET-KEY": "Nn9uKqzFbFfqaxXgyXkCLrlETfF1DMN6STNvX4jG"
                }
            )
            alpaca_response.raise_for_status()
            print("✅ Alpaca order placed.")
        else:
            print("ℹ️ Alert received, but not an entry signal. No trade executed.")

        return jsonify({"status": "Logged and processed"}), 200

    except Exception as e:
        print(f"❌ Error: {e}")
        return jsonify({"error": "Failed to process", "details": str(e)}), 400

if __name__ == '__main__':
    print("🚀 Flask server is starting at http://localhost:5000")
    app.run(host='0.0.0.0', port=5000)
