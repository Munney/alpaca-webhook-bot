from flask import Flask, request, jsonify
import requests
import os

app = Flask(__name__)
app.url_map.strict_slashes = False  # accept /webhook and /webhook/

# === Config (env first; set these in Render) ===
ALPACA_API_KEY    = os.getenv("ALPACA_API_KEY",    "YOUR_ALPACA_API_KEY")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY", "YOUR_ALPACA_SECRET_KEY")
ALPACA_BASE_URL   = os.getenv("ALPACA_BASE_URL",   "https://paper-api.alpaca.markets")  # no /v2 here
GS_WEBHOOK_URL    = os.getenv("GOOGLE_SHEETS_WEBHOOK_URL", "https://script.google.com/macros/s/REPLACE_ME/exec")

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
    if s in ("long", "long entry", "entry long"):
        return "long entry"
    if s in ("short", "short entry", "entry short"):
        return "short entry"
    if s in ("exit long", "close long"):
        return "exit long"
    if s in ("exit short", "close short"):
        return "exit short"
    if s == "exit":
        return "exit"
    return s

def side_for_sheet(action: str) -> str:
    """Side we log to Sheets (READABLE, not the Alpaca side)."""
    return (
        "BUY"  if action in ("long entry", "exit short") else
        "SELL" if action in ("short entry", "exit long") else
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

        # Accept either 'ticker' or 'symbol'; accept both 'alert' and 'signal'
        symbol    = (data.get("ticker") or data.get("symbol") or "").upper()
        timeframe = str(data.get("timeframe", ""))
        version   = str(data.get("version") or data.get("strategy") or "")
        price     = str(data.get("price", ""))  # OK as string
        qty       = int(data.get("qty", 1))

        alert_raw = (data.get("alert") or data.get("signal") or "").strip()
        action    = normalize_alert(alert_raw)

        if not symbol or not action:
            return jsonify({"status": "error", "message": "Missing ticker/signal"}), 400

        # Side that goes to Sheets (for human readability)
        sheet_side = side_for_sheet(action)

        # --- Log to Google Sheets (best effort, non-blocking) ---
        try:
            gs_payload = {
                "ticker":    symbol,
                "timeframe": timeframe,
                "strategy":  version,
                "type":      action.title(),   # e.g., "Exit Long"
                "side":      sheet_side,       # BUY/SELL/CLOSE/—
                "price":     price
            }
            print(f"🧾 gs_payload -> {gs_payload}")
            gs_resp = requests.post(GS_WEBHOOK_URL, json=gs_payload, timeout=10)
            print(f"📤 Sheets: {gs_resp.status_code} - {gs_resp.text}")
        except Exception as e:
            print(f"⚠️ Sheets log error (non-blocking): {e}")

        # --- Trading logic (Alpaca) ---
        if action in ("long entry", "short entry"):
            # 1) Account check
            acct = requests.get(f"{ALPACA_BASE_URL}/v2/account", headers=HEADERS, timeout=10)
            print(f"👤 Account: {acct.status_code} - {acct.text}")
            if acct.status_code >= 300:
                return jsonify({"status": "error", "message": "Account check failed", "alpaca": acct.text}), 400
            acct_j = acct.json()
            if acct_j.get("status") != "ACTIVE" or acct_j.get("trading_blocked") or acct_j.get("account_blocked"):
                return jsonify({"status": "error", "message": "Account not allowed to trade", "alpaca": acct.text}), 400

            # 2) Asset check
            asset = requests.get(f"{ALPACA_BASE_URL}/v2/assets/{symbol}", headers=HEADERS, timeout=10)
            print(f"📈 Asset: {asset.status_code} - {asset.text}")
            if asset.status_code >= 300:
                return jsonify({"status": "error", "message": "Asset lookup failed", "alpaca": asset.text}), 400
            a = asset.json()
            if not a.get("tradable", False):
                return jsonify({"status": "error", "message": f"{symbol} not tradable", "alpaca": asset.text}), 400
            if action == "short entry" and not a.get("shortable", False):
                return jsonify({"status": "error", "message": f"{symbol} not shortable or account not permitted"}), 400

            # 3) Market clock
            clock = requests.get(f"{ALPACA_BASE_URL}/v2/clock", headers=HEADERS, timeout=10)
            print(f"🕒 Clock: {clock.status_code} - {clock.text}")
            is_open = (clock.status_code < 300 and clock.json().get("is_open") is True)

            side = "buy" if action == "long entry" else "sell"
            order_payload = {
                "symbol": symbol,
                "qty": str(qty),
                "side": side,
                "type": "market",
                "time_in_force": "day",
                "extended_hours": not is_open   # allow fills outside RTH
            }

            print(f"🧾 Alpaca ENTRY payload: {order_payload}")
            resp = requests.post(f"{ALPACA_BASE_URL}/v2/orders", json=order_payload, headers=HEADERS, timeout=15)
            print(f"🟦 Alpaca ENTRY resp: {resp.status_code} - {resp.text}")
            if resp.status_code >= 300:
                return jsonify({"status": "error", "message": "Alpaca entry rejected", "alpaca": resp.text}), 400

            return jsonify({"status": "ok", "action": action, "alpaca": resp.json()}), 200

        elif action in ("exit long", "exit short", "exit"):
            # Always attempt to close the whole position
            print(f"🧾 Alpaca CLOSE position: {symbol}")
            resp = requests.delete(f"{ALPACA_BASE_URL}/v2/positions/{symbol}", headers=HEADERS, timeout=10)
            print(f"🟦 Alpaca CLOSE resp: {resp.status_code} - {resp.text}")
            if resp.status_code >= 300:
                return jsonify({"status": "error", "message": "Alpaca close rejected", "alpaca": resp.text}), 400
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
