import os
import json
import time
import asyncio
import threading
import logging

from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from pyquotex.stable_api import Quotex


# ==================================================
# CONFIGURATION
# ==================================================

HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "8000"))

DEFAULT_ASSET = os.getenv("QUOTEX_ASSET", "EURUSD").upper()

ALLOWED_TIMEFRAMES = {60, 120, 300, 900, 1800}

MAX_CANDLES = 5000
MAX_TICKS = 10000

RECONNECT_DELAY = 10
MAX_RECONNECT_DELAY = 60

PRACTICE_ACCOUNT = True

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

logger = logging.getLogger("bot2.backend")


# ==================================================
# STATE
# ==================================================

state = {
    "connected": False,
    "status": "Starting",
    "asset": DEFAULT_ASSET,
    "price": None,
    "tick_time": None,
    "candles": [],
    "error": None,
}

lock = threading.RLock()

ticks = deque(maxlen=MAX_TICKS)
historical_candles = deque(maxlen=MAX_CANDLES)


# ==================================================
# HELPERS
# ==================================================

def safe_float(value):
    number = float(value)

    if not (number == number):
        raise ValueError("Invalid numeric value")

    if number in (float("inf"), float("-inf")):
        raise ValueError("Infinite value")

    return number


def set_status(status, error=None, connected=None):

    with lock:
        state["status"] = status
        state["error"] = error

        if connected is not None:
            state["connected"] = connected


def ema(values, period):

    if len(values) < period:
        return None

    multiplier = 2 / (period + 1)

    result = sum(values[:period]) / period

    for value in values[period:]:
        result = (
            value * multiplier
            + result * (1 - multiplier)
        )

    return result


def rsi(values, period=14):

    if len(values) <= period:
        return None

    changes = [
        values[i] - values[i - 1]
        for i in range(1, len(values))
    ]

    gains = [max(x, 0) for x in changes]
    losses = [max(-x, 0) for x in changes]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(gains)):

        avg_gain = (
            avg_gain * (period - 1) + gains[i]
        ) / period

        avg_loss = (
            avg_loss * (period - 1) + losses[i]
        ) / period

    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0

    rs = avg_gain / avg_loss

    return 100 - (100 / (1 + rs))


# ==================================================
# CANDLE MANAGEMENT
# ==================================================

def build_candles(period):

    if period not in ALLOWED_TIMEFRAMES:
        raise ValueError("Unsupported timeframe")

    with lock:
        history = list(historical_candles)
        live_ticks = list(ticks)

    # Keep historical candles as the base.
    base = {}

    for candle in history:

        timestamp = int(candle["time"])

        base[timestamp] = {
            "time": timestamp,
            "open": safe_float(candle["open"]),
            "high": safe_float(candle["high"]),
            "low": safe_float(candle["low"]),
            "close": safe_float(candle["close"]),
        }

    # Merge live ticks into their corresponding minute candles.
    grouped = {}

    for tick in live_ticks:

        timestamp = float(tick["time"])
        price = safe_float(tick["price"])

        minute = int(timestamp // 60) * 60

        grouped.setdefault(minute, []).append(
            (timestamp, price)
        )

    for minute, values in grouped.items():

        values.sort(key=lambda item: item[0])

        prices = [item[1] for item in values]

        if minute in base:

            candle = base[minute]

            candle["high"] = max(
                candle["high"],
                max(prices),
            )

            candle["low"] = min(
                candle["low"],
                min(prices),
            )

            candle["close"] = prices[-1]

        else:

            base[minute] = {
                "time": minute,
                "open": prices[0],
                "high": max(prices),
                "low": min(prices),
                "close": prices[-1],
            }

    # Aggregate minute candles into selected timeframe.
    aggregated = {}

    for timestamp, candle in sorted(base.items()):

        bucket = (timestamp // period) * period

        if bucket not in aggregated:

            aggregated[bucket] = {
                "time": bucket,
                "open": candle["open"],
                "high": candle["high"],
                "low": candle["low"],
                "close": candle["close"],
            }

        else:

            current = aggregated[bucket]

            current["high"] = max(
                current["high"],
                candle["high"],
            )

            current["low"] = min(
                current["low"],
                candle["low"],
            )

            current["close"] = candle["close"]

    return list(aggregated.values())[-100:]


# ==================================================
# ANALYSIS
# ==================================================

def analyze(candles):

    closes = [
        candle["close"]
        for candle in candles
    ]

    ema9 = ema(closes, 9)
    ema21 = ema(closes, 21)
    rsi_value = rsi(closes, 14)

    signal = "WAIT"
    reasons = []

    if (
        ema9 is not None
        and ema21 is not None
        and rsi_value is not None
    ):

        if ema9 > ema21 and rsi_value >= 50:

            signal = "BUY"
            reasons.append("EMA 9 above EMA 21")
            reasons.append("RSI bullish momentum")

        elif ema9 < ema21 and rsi_value <= 50:

            signal = "SELL"
            reasons.append("EMA 9 below EMA 21")
            reasons.append("RSI bearish momentum")

        else:

            reasons.append("Indicators are mixed")

    return {
        "signal": signal,
        "ema9": ema9,
        "ema21": ema21,
        "rsi": rsi_value,
        "reasons": reasons,
        "candles_available": len(candles),
    }


# ==================================================
# HTTP API
# ==================================================

class Handler(BaseHTTPRequestHandler):

    def send_json(self, data, status=200):

        body = json.dumps(data).encode("utf-8")

        self.send_response(status)

        self.send_header(
            "Content-Type",
            "application/json; charset=utf-8",
        )

        self.send_header(
            "Cache-Control",
            "no-store",
        )

        # Same-origin dashboard is preferred.
        # Configure CORS_ALLOWED_ORIGIN if hosted separately.
        origin = os.getenv("CORS_ALLOWED_ORIGIN")

        if origin:
            self.send_header(
                "Access-Control-Allow-Origin",
                origin,
            )

        self.send_header(
            "Access-Control-Allow-Headers",
            "Content-Type",
        )

        self.send_header(
            "Access-Control-Allow-Methods",
            "GET, OPTIONS",
        )

        self.send_header(
            "Content-Length",
            str(len(body)),
        )

        self.end_headers()

        self.wfile.write(body)

    def do_OPTIONS(self):

        self.send_json({"ok": True})

    def do_GET(self):

        parsed = urlparse(self.path)

        if parsed.path == "/api/status":

            with lock:

                result = {
                    "connected": state["connected"],
                    "status": state["status"],
                    "asset": state["asset"],
                    "price": state["price"],
                    "tick_time": state["tick_time"],
                    "error": state["error"],
                }

            self.send_json(result)
            return

        if parsed.path == "/api/market":

            params = parse_qs(parsed.query)

            try:
                period = int(
                    params.get("tf", ["60"])[0]
                )

            except (ValueError, TypeError):
                self.send_json(
                    {"error": "Invalid timeframe"},
                    400,
                )
                return

            if period not in ALLOWED_TIMEFRAMES:

                self.send_json(
                    {"error": "Unsupported timeframe"},
                    400,
                )
                return

            with lock:

                connected = state["connected"]
                status = state["status"]
                asset = state["asset"]
                price = state["price"]
                tick_time = state["tick_time"]
                error = state["error"]

            try:

                candles = build_candles(period)
                analysis = analyze(candles)

            except Exception:

                logger.exception("Market analysis failed")

                self.send_json(
                    {"error": "Market analysis unavailable"},
                    500,
                )
                return

            now = time.time()

            data_age = (
                now - tick_time
                if tick_time is not None
                else None
            )

            fresh = (
                data_age is not None
                and 0 <= data_age <= 30
            )

            self.send_json({
                "connected": connected,
                "status": status,
                "asset": asset,
                "price": price,
                "tick_time": tick_time,
                "data_age_seconds": data_age,
                "data_fresh": fresh,
                "timeframe": period,
                "candles": candles,
                "analysis": analysis,
                "error": error,
            })

            return

        self.send_json(
            {"error": "Not found"},
            404,
        )

    def log_message(self, format, *args):
        logger.info(
            "HTTP %s - %s",
            self.address_string(),
            args,
        )


# ==================================================
# QUOTEX CONNECTION
# ==================================================

def load_credentials():

    credentials_path = os.path.expanduser(
        "~/.config/quotex/credentials"
    )

    if os.path.exists(credentials_path):

        with open(
            credentials_path,
            "r",
            encoding="utf-8",
        ) as file:

            for line in file:

                line = line.strip()

                if (
                    line
                    and "=" in line
                    and not line.startswith("#")
                ):

                    key, value = line.split("=", 1)

                    os.environ.setdefault(
                        key.strip(),
                        value.strip(),
                    )

    email = os.getenv("QUOTEX_EMAIL")
    password = os.getenv("QUOTEX_PASSWORD")

    return email, password


async def quotex_worker():

    email, password = load_credentials()

    if not email or not password:

        set_status(
            "Credentials missing",
            "Configure QUOTEX_EMAIL and QUOTEX_PASSWORD",
            False,
        )

        logger.error("Quotex credentials missing")
        return

    delay = RECONNECT_DELAY

    while True:

        client = None

        try:

            set_status(
                "Connecting",
                None,
                False,
            )

            client = Quotex(
                email=email,
                password=password,
                lang="en",
            )

            # Practice account only.
            client.set_account_mode("PRACTICE")

            result = await client.connect()

            if not result or not result[0]:

                raise ConnectionError(
                    "Quotex connection was unsuccessful"
                )

            with lock:

                state["connected"] = True
                state["status"] = "Connected"
                state["error"] = None

                state["asset"] = DEFAULT_ASSET

            logger.info("Quotex connected")

            delay = RECONNECT_DELAY

            # Load historical candles.
            try:

                history = await client.get_historical_candles(
                    asset=DEFAULT_ASSET,
                    amount_of_seconds=7200,
                    period=60,
                    timeout=30,
                    max_workers=1,
                )

                history = sorted(
                    history,
                    key=lambda candle: candle["time"],
                )

                clean_history = []

                for candle in history:

                    clean_history.append({
                        "time": int(candle["time"]),
                        "open": safe_float(candle["open"]),
                        "high": safe_float(candle["high"]),
                        "low": safe_float(candle["low"]),
                        "close": safe_float(candle["close"]),
                    })

                with lock:

                    historical_candles.clear()

                    historical_candles.extend(
                        clean_history
                    )

                logger.info(
                    "Historical candles loaded: %s",
                    len(clean_history),
                )

            except Exception:

                logger.exception(
                    "Historical candle loading failed"
                )

            await client.start_realtime_price(
                DEFAULT_ASSET,
                60,
            )

            while True:

                prices = await client.get_realtime_price(
                    DEFAULT_ASSET
                )

                if prices:

                    clean_ticks = []

                    for tick in prices:

                        clean_ticks.append({
                            "time": safe_float(tick["time"]),
                            "price": safe_float(tick["price"]),
                        })

                    clean_ticks.sort(
                        key=lambda item: item["time"]
                    )

                    with lock:

                        for item in clean_ticks:

                            if (
                                not ticks
                                or (
                                    ticks[-1]["time"] != item["time"]
                                    or ticks[-1]["price"] != item["price"]
                                )
                            ):

                                ticks.append(item)

                        latest = clean_ticks[-1]

                        state["price"] = latest["price"]
                        state["tick_time"] = latest["time"]
                        state["status"] = "Connected"
                        state["connected"] = True
                        state["error"] = None

                await asyncio.sleep(1)

        except asyncio.CancelledError:
            raise

        except Exception as exc:

            logger.exception("Quotex connection error")

            set_status(
                "Reconnecting",
                "Quotex connection interrupted",
                False,
            )

        finally:

            if client is not None:

                try:
                    await client.close()

                except Exception:
                    logger.warning(
                        "Quotex client close failed"
                    )

        logger.info(
            "Retrying Quotex connection in %s seconds",
            delay,
        )

        await asyncio.sleep(delay)

        delay = min(
            delay * 2,
            MAX_RECONNECT_DELAY,
        )


# ==================================================
# SERVER
# ==================================================

def run_server():

    server = ThreadingHTTPServer(
        (HOST, PORT),
        Handler,
    )

    logger.info(
        "Dashboard API listening on %s:%s",
        HOST,
        PORT,
    )

    try:
        server.serve_forever()

    finally:
        server.server_close()


async def main():

    server_thread = threading.Thread(
        target=run_server,
        daemon=True,
    )

    server_thread.start()

    try:
        await quotex_worker()

    finally:
        logger.info("Backend shutting down")


if __name__ == "__main__":

    try:
        asyncio.run(main())

    except KeyboardInterrupt:
        logger.info("Backend stopped")
