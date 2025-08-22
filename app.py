from flask import Flask, request, jsonify
import requests
import os
import time

app = Flask(__name__)
app.url_map.strict_slashes = False  # accept /webhook and /webhook/

# === Config (env first; set these in Render) ===
ALPACA_API_KEY       = os.getenv("ALPACA_API_KEY",              "YOUR_ALPACA_API_KEY")
ALPACA_SECRET_KEY    = os.getenv("ALPACA_SECRET_KEY",           "YOUR_ALPACA_SECRET_KEY")
ALPACA_BASE_URL      = os.getenv("ALPACA_BASE_URL",             "https://paper-api.alpaca.markets")
GS_WEBHOOK_URL       = os.getenv("GOOGLE_SHEETS_WEBHOOK_URL",   "https://script.google.com/macros/s/REPLACE_ME/exec")
TRADE_RISK_PERCENT   = float(os.getenv("TRADE_RISK_PERCENT", "1.0")) # Risk 1% of buying power per trade

HEADERS = {
    "APCA-API-KEY-ID": ALPACA_API_KEY,
    "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY
}

@app.before_request
def log_request():
    try:
        print(f"➡️ {request.method} {request.path}")
    except Exception:
        pass

@app.route("/", methods=["GET"])
def root():
    return "✅ Alpaca Webhook Bot is live!", 200

def normalize_alert(s: str) -> str:
    """Map whatever TV sends to our four actions."""
    if not s:
        return ""
    s = s.replace("_", " ").strip().lower()
    if s in ("long", "long entry", "entry long", "buy"):
        return "long entry"
    if s in ("short", "short entry", "entry short", "sell"):
        return "short entry"
    if s in ("exit long", "close long"):
        return "exit long"
    if s in ("exit short", "close short"):
        return "exit short"
    if s in ("exit", "close"):
        return "exit"
    return s

def side_for_sheet(action: str) -> str:
    """Side we log to Sheets (READABLE, not the Alpaca side)."""
    return (
        "BUY"   if action in ("long entry", "exit short") else
        "SELL"  if action in ("short entry", "exit long") else
        "CLOSE" if action == "exit" else "—"
    )

@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        raw = request.data.decode("utf-8", errors="ignore")
        print(f"📦 Raw body:\n{raw}")
        data = request.get_json(force=True, silent=True)
        print(f"📋 Parsed JSON:\n{data}")

        if not data:
            return jsonify({"status": "error", "message": "Invalid or empty JSON"}), 400

        # === Parse webhook data ===
        symbol    = (data.get("ticker") or data.get("symbol") or "").upper()
        timeframe = str(data.get("timeframe", ""))
        version   = str(data.get("version") or data.get("strategy") or "")
        price_str = str(data.get("price", "0"))
        signal_id = str(data.get("signal_id", "")) # For idempotency
        alert_raw = (data.get("alert") or data.get("signal") or "").strip()
        action    = normalize_alert(alert_raw)

        if not symbol or not action:
            return jsonify({"status": "error", "message": "Missing symbol or alert action"}), 400

        # --- Log to Google Sheets (best effort, non-blocking) ---
        try:
            gs_payload = {
                "ticker":    symbol,
                "timeframe": timeframe,
                "strategy":  version,
                "type":      action.title(),
                "side":      side_for_sheet(action),
                "price":     price_str
            }
            print(f"🧾 gs_payload -> {gs_payload}")
            gs_resp = requests.post(GS_WEBHOOK_URL, json=gs_payload, timeout=10)
            print(f"📤 Sheets: {gs_resp.status_code} - {gs_resp.text}")
        except Exception as e:
            print(f"⚠️ Sheets log error (non-blocking): {e}")

        # === Trading logic (Alpaca) ===
        if action in ("long entry", "short entry"):
            # 1) Account check
            acct = requests.get(f"{ALPACA_BASE_URL}/v2/account", headers=HEADERS, timeout=10)
            if not acct.ok:
                return jsonify({"status": "error", "message": "Account check failed", "alpaca": acct.text}), 400
            acct_j = acct.json()
            if acct_j.get("status") != "ACTIVE" or acct_j.get("trading_blocked") or acct_j.get("account_blocked"):
                return jsonify({"status": "error", "message": "Account not allowed to trade", "alpaca": acct.text}), 400
            
            # === Dynamic Quantity Calculation ===
            try:
                price = float(price_str)
                buying_power = float(acct_j.get("buying_power", 0))
                trade_value = buying_power * (TRADE_RISK_PERCENT / 100.0)
                if price <= 0:
                    return jsonify({"status": "error", "message": f"Invalid price for calculation: {price}"}), 400
                qty = round(trade_value / price, 6) # Fractional shares are supported
                print(f"💰 Calculated Quantity: {qty} of {symbol} @ ${price} (Risk: {TRADE_RISK_PERCENT}%)")
            except (ValueError, ZeroDivisionError) as e:
                print(f"⚠️ Qty calc error: {e}")
                return jsonify({"status": "error", "message": "Failed to calculate quantity"}), 400

            if qty <= 0:
                return jsonify({"status": "ok", "message": f"Calculated quantity is {qty}. No order placed."}), 200

            # === Idempotency Key ===
            if not signal_id:
                signal_id = str(int(time.time()))
                print(f"⚠️ Missing 'signal_id' from webhook. Using timestamp '{signal_id}' as fallback.")
            
            client_order_id = f"tv_{symbol.lower()}_{action.replace(' ', '')}_{signal_id}"[:48]
            print(f"🔑 Client Order ID: {client_order_id}")

            # 2) Asset check
            asset = requests.get(f"{ALPACA_BASE_URL}/v2/assets/{symbol}", headers=HEADERS, timeout=10)
            if not asset.ok:
                return jsonify({"status": "error", "message": f"Asset {symbol} not found", "alpaca": asset.text}), 400
            asset_j = asset.json()
            if not asset_j.get("tradable", False):
                return jsonify({"status": "error", "message": f"{symbol} not tradable", "alpaca": asset.text}), 400
            if action == "short entry" and not asset_j.get("shortable", False):
                return jsonify({"status": "error", "message": f"{symbol} not shortable"}), 400
            
            # 3) Market clock & Order Logic
            clock = requests.get(f"{ALPACA_BASE_URL}/v2/clock", headers=HEADERS, timeout=10)
            is_open = clock.ok and clock.json().get("is_open", False)

            side = "buy" if action == "long entry" else "sell"
            
            order_payload = {
                "symbol": symbol,
                "qty": str(qty),
                "side": side,
                "time_in_force": "day",
                "client_order_id": client_order_id
            }

            if is_open:
                # During market hours, send a simple market order
                order_payload["type"] = "market"
            else:
                # Outside market hours, send a limit order to participate in extended hours
                order_payload["type"] = "limit"
                order_payload["limit_price"] = str(price)
                order_payload["extended_hours"] = True

            print(f"🧾 Alpaca ENTRY payload: {order_payload}")
            resp = requests.post(f"{ALPACA_BASE_URL}/v2/orders", json=order_payload, headers=HEADERS, timeout=15)
            if not resp.ok:
                return jsonify({"status": "error", "message": "Alpaca entry rejected", "alpaca": resp.text}), 400

            return jsonify({"status": "ok", "action": action, "alpaca": resp.json()}), 200

        elif action in ("exit long", "exit short", "exit"):
            # Always attempt to close the whole position for the given symbol
            print(f"🧾 Alpaca CLOSE position: {symbol}")
            resp = requests.delete(f"{ALPACA_BASE_URL}/v2/positions/{symbol}", headers=HEADERS, timeout=10)
            if not resp.ok:
                return jsonify({"status": "error", "message": "Alpaca close rejected", "alpaca": resp.text}), 400
            return jsonify({"status": "ok", "action": action, "alpaca": resp.json()}), 200

        return jsonify({"status": "error", "message": f"Unknown action: {action}"}), 400

    except Exception as e:
        print(f"❌ Exception: {e}")
        return jsonify({"status": "error", "message": str(e)}), 400

@app.route("/healthz", methods=["GET"])
def healthz():
    return jsonify({"ok": True}), 200

if __name__ == "__main__":
    print("🚀 Flask server is starting at http://localhost:5000")
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))