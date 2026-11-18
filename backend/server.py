import os
import json
import time
import asyncio
import threading
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from pyquotex.stable_api import Quotex

HOST = "0.0.0.0"
PORT = int(os.getenv("PORT", "8000"))
ASSET = os.getenv("QUOTEX_ASSET", "EURUSD")

state = {
    "connected": False,
    "status": "Starting",
    "asset": ASSET,
    "price": None,
    "tick_time": None,
    "candles": [],
    "error": None,
}

lock = threading.Lock()
ticks = deque(maxlen=10000)
historical_candles = deque(maxlen=5000)


def ema(values, period):
    if len(values) < period:
        return None

    multiplier = 2 / (period + 1)
    result = sum(values[:period]) / period

    for value in values[period:]:
        result = (value - result) * multiplier + result

    return result


def rsi(values, period=14):
    if len(values) < period + 1:
        return None

    changes = [
        values[i] - values[i - 1]
        for i in range(1, len(values))
    ]

    gains = [max(change, 0) for change in changes]
    losses = [max(-change, 0) for change in changes]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0

    return 100 - (100 / (1 + avg_gain / avg_loss))


def build_candles(period):
    base = {}

    for candle in list(historical_candles):
        base[int(candle["time"])] = {
            "time": int(candle["time"]),
            "open": float(candle["open"]),
            "high": float(candle["high"]),
            "low": float(candle["low"]),
            "close": float(candle["close"]),
        }

    grouped = {}
    for tick in list(ticks):
        start = int(tick["time"] // 60) * 60
        grouped.setdefault(start, []).append(tick["price"])

    for start, prices in grouped.items():
        base[start] = {
            "time": start,
            "open": prices[0],
            "high": max(prices),
            "low": min(prices),
            "close": prices[-1],
        }

    aggregated = {}
    for start, candle in sorted(base.items()):
        bucket = (start // period) * period

        if bucket not in aggregated:
            aggregated[bucket] = {
                "time": bucket,
                "open": candle["open"],
                "high": candle["high"],
                "low": candle["low"],
                "close": candle["close"],
            }
        else:
            aggregated[bucket]["high"] = max(aggregated[bucket]["high"], candle["high"])
            aggregated[bucket]["low"] = min(aggregated[bucket]["low"], candle["low"])
            aggregated[bucket]["close"] = candle["close"]

    return list(aggregated.values())[-100:]



def analyze(candles):
    closes = [c["close"] for c in candles]

    e9 = ema(closes, 9)
    e21 = ema(closes, 21)
    r = rsi(closes, 14)

    signal = "WAIT"
    reasons = []

    if e9 is not None and e21 is not None and r is not None:
        if e9 > e21 and r >= 50:
            signal = "BUY"
            reasons.append("EMA 9 above EMA 21")
            reasons.append("RSI bullish momentum")
        elif e9 < e21 and r <= 50:
            signal = "SELL"
            reasons.append("EMA 9 below EMA 21")
            reasons.append("RSI bearish momentum")
        else:
            reasons.append("Indicators are mixed")

    return {
        "signal": signal,
        "ema9": e9,
        "ema21": e21,
        "rsi": r,
        "reasons": reasons,
        "candles_available": len(candles),
    }


class Handler(BaseHTTPRequestHandler):
    def send_json(self, data, status=200):
        body = json.dumps(data).encode()

        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_json({"ok": True})

    def do_GET(self):
        parsed = urlparse(self.path)

        if parsed.path == "/api/status":
            with lock:
                self.send_json({
                    "connected": state["connected"],
                    "status": state["status"],
                    "asset": state["asset"],
                    "price": state["price"],
                    "tick_time": state["tick_time"],
                    "error": state["error"],
                })
            return

        if parsed.path == "/api/market":
            params = parse_qs(parsed.query)
            period = int(params.get("tf", ["60"])[0])

            if period not in (60, 120, 300, 900, 1800):
                self.send_json({"error": "Unsupported timeframe"}, 400)
                return

            with lock:
                candle_data = build_candles(period)
                result = {
                    "connected": state["connected"],
                    "status": state["status"],
                    "asset": state["asset"],
                    "price": state["price"],
                    "tick_time": state["tick_time"],
                    "timeframe": period,
                    "candles": candle_data,
                    "analysis": analyze(candle_data),
                    "error": state["error"],
                }

            self.send_json(result)
            return

        self.send_json({"error": "Not found"}, 404)

    def log_message(self, format, *args):
        pass


async def quotex_worker():
    credentials_path = os.path.expanduser("~/.config/quotex/credentials")

    if os.path.exists(credentials_path):
        with open(credentials_path, "r") as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    key, value = line.split("=", 1)
                    os.environ.setdefault(key.strip(), value.strip())

    email = os.getenv("QUOTEX_EMAIL")
    password = os.getenv("QUOTEX_PASSWORD")

    if not email or not password:
        with lock:
            state["status"] = "Credentials missing"
            state["error"] = "Set QUOTEX_EMAIL and QUOTEX_PASSWORD environment variables"
        return

    client = Quotex(email=email, password=password)
    client.set_account_mode("PRACTICE")

    try:
        with lock:
            state["status"] = "Connecting"

        result = await client.connect()

        if not result or not result[0]:
            with lock:
                state["status"] = "Connection failed"
                state["error"] = str(result)
            return

        with lock:
            state["connected"] = True
            state["status"] = "Connected"
            state["error"] = None

        # Load historical candles for indicator calculations
        try:
            history = await client.get_historical_candles(
                asset=ASSET,
                amount_of_seconds=7200,
                period=60,
                timeout=30,
                max_workers=1,
            )

            history.sort(key=lambda c: c["time"])

            with lock:
                historical_candles.clear()
                historical_candles.extend(history)

            print(f"Historical candles loaded: {len(history)}")

        except Exception as history_error:
            print(f"Historical data unavailable: {history_error}")

        await client.start_realtime_price(ASSET, 60)

        while True:
            prices = await client.get_realtime_price(ASSET)

            if prices:
                for tick in prices:
                    item = {
                        "time": float(tick["time"]),
                        "price": float(tick["price"]),
                    }

                    if not ticks or (
                        ticks[-1]["time"] != item["time"]
                        or ticks[-1]["price"] != item["price"]
                    ):
                        ticks.append(item)

                latest = prices[-1]

                with lock:
                    state["price"] = float(latest["price"])
                    state["tick_time"] = float(latest["time"])
                    state["status"] = "Connected"
                    state["error"] = None

            await asyncio.sleep(1)

    except Exception as exc:
        with lock:
            state["connected"] = False
            state["status"] = "Connection error"
            state["error"] = str(exc)

    finally:
        try:
            await client.close()
        except Exception:
            pass


def run_server():
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Dashboard API running on port {PORT}")
    server.serve_forever()


async def main():
    threading.Thread(target=run_server, daemon=False).start()
    await quotex_worker()


if __name__ == "__main__":
    asyncio.run(main())
