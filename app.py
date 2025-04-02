
from flask import Flask, request, jsonify
import requests
import os

app = Flask(__name__)

# Load Alpaca API keys from environment variables or replace here
ALPACA_API_KEY = os.getenv("ALPACA_API_KEY", "YOUR_ALPACA_API_KEY")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY", "YOUR_ALPACA_SECRET_KEY")
BASE_URL = "https://paper-api.alpaca.markets"  # Use paper trading base URL

HEADERS = {
    "APCA-API-KEY-ID": ALPACA_API_KEY,
    "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY
}

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json

    print("Received alert:", data)

    symbol = data.get("ticker")
    signal = data.get("signal")
    qty = int(data.get("qty", 1))  # default quantity

    if signal == "LONG":
        order = place_order(symbol, qty, "buy")
    elif signal == "SHORT":
        order = place_order(symbol, qty, "sell")
    elif signal == "EXIT":
        order = close_position(symbol)
    else:
        return jsonify({"status": "error", "message": "Invalid signal"}), 400

    return jsonify({"status": "ok", "order": order})

def place_order(symbol, qty, side):
    url = f"{BASE_URL}/v2/orders"
    order_data = {
        "symbol": symbol,
        "qty": qty,
        "side": side,
        "type": "market",
        "time_in_force": "gtc"
    }
    r = requests.post(url, json=order_data, headers=HEADERS)
    return r.json()

def close_position(symbol):
    url = f"{BASE_URL}/v2/positions/{symbol}"
    r = requests.delete(url, headers=HEADERS)
    return r.json()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
