from flask import Flask, request, jsonify
import requests
import os

app = Flask(__name__)
app.url_map.strict_slashes = False  # accept /webhook and /webhook/

# === Config (env first, fallback to placeholders) ===
ALPACA_API_KEY     = os.getenv("ALPACA_API_KEY",     "YOUR_ALPACA_API_KEY")
ALPACA_SECRET_KEY  = os.getenv("ALPACA_SECRET_KEY",  "YOUR_ALPACA_SECRET_KEY")
ALPACA_BASE_URL    = os.getenv("ALPACA_BASE_URL",    "https://paper-api.alpaca.markets")  # live: https://api.alpaca.markets
GS_WEBHOOK_URL     = os.getenv("GOOGLE_SHEETS_WEBHOOK_URL", "https://script.google.com/macros/s/REPLACE_ME/exec")

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
    if not s:
        return ""
    s = s.replace("_", " ").strip().lower()
    if s in ("long", "long entry", "entry long"):     return "long entry"
    if s in ("short", "short entry", "entry short"):  return "short entry"
    if s in ("exit long", "close long"):              return "exit long"
    if s in ("exit short", "close short"):            return "exit short"
    if s == "exit":                                   return "exit"
    return s

@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        raw = request.data.decode("utf-8", errors="ignore")
        print(f"📦 Raw body:\n{raw}")
        data = request.get_json(force=True, silent=True)
        print(f"📋 Parsed JSON:\n{data}")

        if not data:
            return jsonify({"status": "error", "message": "Invalid or empty JSON"}), 400

        # --- Common fields ---
        symbol     = (data.get("ticker") or data.get("symbol") or "").upper()
        timeframe  = str(data.get("timeframe", ""))
        version    = str(data.get("version") or data.get("strategy") or "")
        price      = str(data.get("price", ""))
        qty        = int(data.get("qty", 1))

        # Accept both "alert" and "signal"
        alert_raw  = (data.get("alert") or data.get("signal") or "").strip()
        action     = normalize_alert(alert_raw)

        if not symbol or not action:
            return jsonify({"status": "error", "message": "Missing ticker/signal"}), 400

        # --- Compute side_for_log (ALWAYS before Sheets) ---
        # Long Entry  -> BUY
        # Short Entry -> SELL
        # Exit Long   -> SELL (close long)
        # Exit Short  -> BUY  (close short)
        # Exit        -> CLOSE (unknown)
        if action == "long entry":
            side_for_log = "BUY"
        elif action == "short entry":
            side_for_log = "SELL"
        elif action == "exit long":
            side_for_log = "SELL"
        elif action == "exit short":
            side_for_log = "BUY"
        elif action == "exit":
            side_for_log = "CLOSE"
        else:
            side_for_log = "—"

        # --- Google Sheets logging (best effort) ---
        try:
            gs_payload = {
                "ticker":   symbol,
                "timeframe": timeframe,
                "strategy":  version,
                "type":      action.title(),  # e.g., "Exit Long"
                "side":      side_for_log,    # BUY/SELL/CLOSE/—
                "price":     price
            }
            print(f"🧾 gs_payload -> {gs_payload}")
            gs_resp = requests.post(GS_WEBHOOK_URL, json=gs_payload, timeout=10)
            print(f"📤 Sheets: {gs_resp.status_code} - {gs_resp.text}")
        except Exception as e:
            print(f"⚠️ Sheets log error (non-blocking): {e}")

        # --- Alpaca execution ---
        if action in ("long entry", "short entry"):
            side = "buy" if action == "long entry" else "sell"
            order_payload = {
                "symbol": symbol,
                "qty": qty,
                "side": side,
                "type": "market",
                "time_in_force": "gtc"
            }
            print(f"🧾 Alpaca order payload (ENTRY): {order_payload}")
            resp = requests.post(f"{ALPACA_BASE_URL}/v2/orders", json=order_payload, headers=HEADERS, timeout=10)
            if resp.status_code >= 300:
                print(f"❌ Alpaca entry error: {resp.status_code} - {resp.text}")
                return jsonify({"status": "error", "message": "Alpaca entry rejected", "alpaca": resp.text}), 400
            print(f"✅ Alpaca entry placed: {side.upper()} {symbol}")
            return jsonify({"status": "ok", "action": action, "alpaca": resp.json()}), 200

        elif action in ("exit long", "exit short", "exit"):
            print(f"🧾 Alpaca close position: {symbol}")
            resp = requests.delete(f"{ALPACA_BASE_URL}/v2/positions/{symbol}", headers=HEADERS, timeout=10)
            if resp.status_code >= 300:
                print(f"❌ Alpaca close error: {resp.status_code} - {resp.text}")
                return jsonify({"status": "error", "message": "Alpaca close rejected", "alpaca": resp.text}), 400
            print(f"✅ Alpaca position closed: {symbol}")
            return jsonify({"status": "ok", "action": action, "alpaca": resp.json()}), 200

        return jsonify({"status": "error", "message": f"Unknown signal: {action}"}), 400

    except Exception as e:
        print(f"❌ Exception: {e}")
        return jsonify({"status": "error", "message": str(e)}), 400

@app.route("/healthz", methods=["GET"])
def healthz():
    return jsonify({"ok": True}), 200

if __name__ == "__main__":
    print("🚀 Flask server is starting at http://localhost:5000")
    app.run(host="0.0.0.0", port=5000)
