from flask import Flask, request, jsonify
import requests
import os

app = Flask(__name__)
app.url_map.strict_slashes = False  # accept /webhook and /webhook/

# === Config ===
ALPACA_API_KEY    = os.getenv("ALPACA_API_KEY", "YOUR_ALPACA_API_KEY")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY", "YOUR_ALPACA_SECRET_KEY")
ALPACA_BASE_URL   = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")  # live: https://api.alpaca.markets

GS_WEBHOOK_URL    = os.getenv("GOOGLE_SHEETS_WEBHOOK_URL", "https://script.google.com/macros/s/REPLACE_ME/exec")

HEADERS = {
    "APCA-API-KEY-ID": ALPACA_API_KEY,
    "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY
}

def normalize_alert(s: str) -> str:
    if not s:
        return ""
    s = s.replace("_", " ").strip().lower()
    if s in ("long", "long entry", "entry long"):     return "long entry"
    if s in ("short", "short entry", "entry short"):  return "short entry"
    if s in ("exit long", "close long"):              return "exit long"
    if s in ("exit short", "close short"):            return "exit short"
    if s == "exit":                                   return "exit"
    return s

@app.route("/", methods=["GET"])
def root():
    return "✅ Alpaca Webhook Bot is live!", 200

@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        raw = request.data.decode("utf-8", errors="ignore")
        print(f"📦 Raw body:\n{raw}")
        data = request.get_json(force=True, silent=True)
        print(f"📋 Parsed JSON:\n{data}")

        if not data:
            return jsonify({"status":"error","message":"Invalid or empty JSON"}), 400

        symbol    = (data.get("ticker") or data.get("symbol") or "").upper()
        timeframe = str(data.get("timeframe", ""))
        version   = str(data.get("version") or data.get("strategy") or "")
        price     = str(data.get("price", ""))
        qty       = int(data.get("qty", 1))
        action    = normalize_alert((data.get("alert") or data.get("signal") or "").strip())

        if not symbol or not action:
            return jsonify({"status":"error","message":"Missing ticker/signal"}), 400

        # --- Decide side for log only (what went to Sheets)
        if action == "long entry":
            side_for_log = "BUY"
        elif action == "short entry":
            side_for_log = "SELL"
        elif action == "exit long":
            side_for_log = "SELL"   # closing long
        elif action == "exit short":
            side_for_log = "BUY"    # buy-to-cover
        elif action == "exit":
            side_for_log = "CLOSE"
        else:
            side_for_log = "—"

        # --- Log to Google Sheets (best effort, non-blocking)
        try:
            gs_payload = {
                "ticker":   symbol,
                "timeframe": timeframe,
                "strategy":  version,
                "type":      action.title(),
                "side":      side_for_log,
                "price":     price
            }
            print(f"🧾 gs_payload -> {gs_payload}")
            gs_resp = requests.post(GS_WEBHOOK_URL, json=gs_payload, timeout=10)
            print(f"📤 Sheets: {gs_resp.status_code} - {gs_resp.text}")
        except Exception as e:
            print(f"⚠️ Sheets log error (non-blocking): {e}")

        # --- Trading logic ---
        if action in ("long entry", "short entry"):
            side = "buy" if action == "long entry" else "sell"
            order_payload = {
                "symbol": symbol,
                "qty": qty,
                "side": side,
                "type": "market",
                "time_in_force": "gtc"
            }
            print(f"🧾 Alpaca ENTRY payload: {order_payload}")
            resp = requests.post(f"{ALPACA_BASE_URL}/v2/orders", json=order_payload, headers=HEADERS, timeout=15)
            print(f"🟦 Alpaca ENTRY resp: {resp.status_code} - {resp.text}")
            if resp.status_code >= 300:
                return jsonify({"status":"error","message":"Alpaca entry rejected","alpaca":resp.text}), 400
            return jsonify({"status":"ok","action":action,"alpaca":resp.json()}), 200

        elif action in ("exit long", "exit short", "exit"):
            # 1) Try to fetch current position
            pos_resp = requests.get(f"{ALPACA_BASE_URL}/v2/positions/{symbol}", headers=HEADERS, timeout=10)
            print(f"🟪 Position GET: {pos_resp.status_code} - {pos_resp.text}")

            if pos_resp.status_code == 404:
                # No open position — nothing to close
                print("ℹ️ No open position, EXIT becomes no-op.")
                return jsonify({"status":"ok","action":action,"message":"No open position"}), 200

            if pos_resp.status_code >= 300:
                return jsonify({"status":"error","message":"Failed to fetch position","alpaca":pos_resp.text}), 400

            pos = pos_resp.json()
            side = pos.get("side")       # "long" or "short"
            pos_qty = pos.get("qty")     # string quantity
            print(f"📊 Current position: side={side}, qty={pos_qty}")

            # 2) Decide proper closing side
            if side == "long":
                close_side = "sell"   # sell to close long
            elif side == "short":
                close_side = "buy"    # buy to cover short
            else:
                print("ℹ️ Unknown or zero position; treating as no-op.")
                return jsonify({"status":"ok","action":action,"message":"No open position"}), 200

            close_payload = {
                "symbol": symbol,
                "qty": pos_qty,
                "side": close_side,
                "type": "market",
                "time_in_force": "gtc"
            }
            print(f"🧾 Alpaca EXIT payload: {close_payload}")
            c_resp = requests.post(f"{ALPACA_BASE_URL}/v2/orders", json=close_payload, headers=HEADERS, timeout=15)
            print(f"🟥 Alpaca EXIT resp: {c_resp.status_code} - {c_resp.text}")
            if c_resp.status_code >= 300:
                # For completeness, if your account allows, you can also attempt DELETE /v2/positions/{symbol}
                return jsonify({"status":"error","message":"Alpaca close rejected","alpaca":c_resp.text}), 400

            return jsonify({"status":"ok","action":action,"alpaca":c_resp.json()}), 200

        else:
            return jsonify({"status":"error","message":f"Unknown signal: {action}"}), 400

    except Exception as e:
        print(f"❌ Exception: {e}")
        return jsonify({"status":"error","message":str(e)}), 400

@app.route("/healthz", methods=["GET"])
def healthz():
    return jsonify({"ok": True}), 200

if __name__ == "__main__":
    print("🚀 Flask server is starting at http://localhost:5000")
    app.run(host="0.0.0.0", port=5000)
