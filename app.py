from flask import Flask, request, jsonify
import requests

app = Flask(__name__)

# === Google Sheets Webhook URL ===
GOOGLE_SHEETS_WEBHOOK_URL = "https://script.google.com/macros/s/AKfycbyXYYuC92u0A59CgEHZnIFW8Jg9-aQMgods2f6AZjt7iIm1v090-Cy5Uuhk0_K6b3X2FQ/exec"

# === Alpaca API Keys ===
ALPACA_API_KEY = "PKDYG000ARPL9C625NQD"
ALPACA_SECRET_KEY = "Nn9uKqzFbFfqaxXgyXkCLrlETfF1DMN6STNvX4jG"
ALPACA_BASE_URL = "https://paper-api.alpaca.markets/v2"

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
        gs_response = requests.post(GOOGLE_SHEETS_WEBHOOK_URL, json=gs_payload)
        print(f"📤 Google Sheets response: {gs_response.status_code} - {gs_response.text}")

        # === Send order to Alpaca ===
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
                    "APCA-API-KEY-ID": ALPACA_API_KEY,
                    "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY
                }
            )
            alpaca_response.raise_for_status()
            print(f"✅ Alpaca order placed: {side.upper()} {ticker}")
        else:
            print("ℹ️ Alert was not an entry signal. No order placed.")

        # ✅ Final return AFTER both Sheets + Alpaca logic
        return json
