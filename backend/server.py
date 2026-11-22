
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

HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8000"))

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DASHBOARD_PATH = os.path.join(BASE_DIR, "web", "index.html")

DEFAULT_ASSET = os.getenv("QUOTEX_ASSET", "EURUSD").upper()

ALLOWED_ASSETS = {
    "EURUSD", "GBPUSD", "USDJPY",
    "EURGBP", "BTCUSD", "USDCHF",
}

ALLOWED_TIMEFRAMES = {60, 300, 900}

MAX_CANDLES = 5000
MAX_TICKS = 10000

RECONNECT_DELAY = 10
MAX_RECONNECT_DELAY = 60
DATA_MAX_AGE = 30

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
    "platform": "quotex",
    "market_type": "forex",
    "asset": DEFAULT_ASSET,
    "price": None,
    "tick_time": None,
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

    if not (-float("inf") < number < float("inf")):
        raise ValueError("Invalid numeric value")

    return number


def get_selection():
    with lock:
        return (
            state["platform"],
            state["market_type"],
            state["asset"],
        )


def set_status(status, error=None, connected=None):
    with lock:
        state["status"] = status
        state["error"] = error

        if connected is not None:
            state["connected"] = connected


def select_market(platform, market_type, asset):
    platform = platform.lower().strip()
    market_type = market_type.lower().strip()
    asset = asset.upper().replace("/", "").replace(" ", "")

    if platform not in {"quotex", "pocket_option"}:
        raise ValueError("Unsupported platform")

    if market_type not in {"forex", "otc"}:
        raise ValueError("Unsupported market type")

    if asset not in ALLOWED_ASSETS:
        raise ValueError("Unsupported asset")

    with lock:
        changed = (
            state["platform"] != platform
            or state["market_type"] != market_type
            or state["asset"] != asset
        )

        if changed:
            state["platform"] = platform
            state["market_type"] = market_type
            state["asset"] = asset
            state["connected"] = False
            state["price"] = None
            state["tick_time"] = None
            state["error"] = None
            state["status"] = "Changing market"

            ticks.clear()
            historical_candles.clear()

    return changed


# ==================================================
# INDICATORS
# ==================================================

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


def ema_series(values, period):
    if len(values) < period:
        return []

    multiplier = 2 / (period + 1)
    result = sum(values[:period]) / period
    output = [None] * (period - 1) + [result]

    for value in values[period:]:
        result = value * multiplier + result * (1 - multiplier)
        output.append(result)

    return output


def rsi(values, period=14):
    if len(values) <= period:
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


def calculate_macd(values):
    fast = ema_series(values, 12)
    slow = ema_series(values, 26)

    if not fast or not slow:
        return None, None

    macd_values = []

    for fast_value, slow_value in zip(fast, slow):
        if fast_value is None or slow_value is None:
            continue

        macd_values.append(fast_value - slow_value)

    if not macd_values:
        return None, None

    signal_values = ema_series(macd_values, 9)

    macd_line = macd_values[-1]
    signal_line = (
        signal_values[-1] if signal_values else None
    )

    return macd_line, signal_line


# ==================================================
# CANDLE MANAGEMENT
# ==================================================

def build_candles(period):
    if period not in ALLOWED_TIMEFRAMES:
        raise ValueError("Unsupported timeframe")

    with lock:
        history = list(historical_candles)
        live_ticks = list(ticks)

    base = {}

    for candle in history:
        try:
            timestamp = int(candle["time"])
            opening = safe_float(candle["open"])
            high = safe_float(candle["high"])
            low = safe_float(candle["low"])
            close = safe_float(candle["close"])

            if high < low:
                continue

            if not (
                low <= opening <= high
                and low <= close <= high
            ):
                continue

            base[timestamp] = {
                "time": timestamp,
                "open": opening,
                "high": high,
                "low": low,
                "close": close,
            }

        except (ValueError, TypeError, KeyError):
            continue

    grouped = {}

    for tick in live_ticks:
        try:
            timestamp = safe_float(tick["time"])
            price = safe_float(tick["price"])
            minute = int(timestamp // 60) * 60

            grouped.setdefault(minute, []).append(
                (timestamp, price)
            )

        except (ValueError, TypeError, KeyError):
            continue

    for minute, values in grouped.items():
        values.sort(key=lambda item: item[0])
        prices = [item[1] for item in values]

        if minute in base:
            candle = base[minute]
            candle["high"] = max(candle["high"], max(prices))
            candle["low"] = min(candle["low"], min(prices))
            candle["close"] = prices[-1]

        else:
            base[minute] = {
                "time": minute,
                "open": prices[0],
                "high": max(prices),
                "low": min(prices),
                "close": prices[-1],
            }

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
            current["high"] = max(current["high"], candle["high"])
            current["low"] = min(current["low"], candle["low"])
            current["close"] = candle["close"]

    return list(aggregated.values())[-100:]


# ==================================================
# ANALYSIS
# ==================================================

def analyze(candles, fresh=True):
    closes = [candle["close"] for candle in candles]

    ema9 = ema(closes, 9)
    ema21 = ema(closes, 21)
    rsi_value = rsi(closes, 14)
    macd_value, macd_signal = calculate_macd(closes)

    signal = "WAIT"
    reasons = []

    if not fresh:
        reasons.append("Market data is stale")

    elif len(candles) < 35:
        reasons.append("Not enough candles for analysis")

    elif (
        ema9 is not None
        and ema21 is not None
        and rsi_value is not None
        and macd_value is not None
        and macd_signal is not None
    ):
        if (
            ema9 > ema21
            and rsi_value >= 50
            and macd_value > macd_signal
        ):
            signal = "BUY"
            reasons.extend([
                "EMA 9 above EMA 21",
                "RSI bullish momentum",
                "MACD bullish confirmation",
            ])

        elif (
            ema9 < ema21
            and rsi_value <= 50
            and macd_value < macd_signal
        ):
            signal = "SELL"
            reasons.extend([
                "EMA 9 below EMA 21",
                "RSI bearish momentum",
                "MACD bearish confirmation",
            ])

        else:
            reasons.append("Indicators are mixed")

    else:
        reasons.append("Indicator data unavailable")

    return {
        "signal": signal,
        "ema9": ema9,
        "ema21": ema21,
        "rsi": rsi_value,
        "macd": macd_value,
        "macd_signal": macd_signal,
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
        self.send_header("Cache-Control", "no-store")

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
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()

        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_json({"ok": True})

    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        # Serve dashboard homepage
        if parsed.path == "/":
            try:
                with open(DASHBOARD_PATH, "rb") as file:
                    body = file.read()

                self.send_response(200)
                self.send_header(
                    "Content-Type",
                    "text/html; charset=utf-8",
                )
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            except FileNotFoundError:
                self.send_json(
                    {"error": "Dashboard file not found"},
                    404,
                )

            return

        if parsed.path == "/api/status":
            with lock:
                result = dict(state)

            self.send_json(result)
            return

        if parsed.path != "/api/market":
            self.send_json({"error": "Not found"}, 404)
            return

        try:
            period = int(params.get("tf", ["60"])[0])
        except (ValueError, TypeError):
            self.send_json({"error": "Invalid timeframe"}, 400)
            return

        if period not in ALLOWED_TIMEFRAMES:
            self.send_json({"error": "Unsupported timeframe"}, 400)
            return

        try:
            platform = params.get("platform", ["quotex"])[0]
            market_type = params.get("market", ["forex"])[0]
            asset = params.get("asset", [DEFAULT_ASSET])[0]

            select_market(platform, market_type, asset)

        except ValueError as exc:
            self.send_json({"error": str(exc)}, 400)
            return

        with lock:
            current = dict(state)

        # These providers are not yet verified or connected.
        if (
            current["platform"] != "quotex"
            or current["market_type"] != "forex"
        ):
            self.send_json({
                "connected": False,
                "status": "UNAVAILABLE",
                "platform": current["platform"],
                "market_type": current["market_type"],
                "asset": current["asset"],
                "price": None,
                "tick_time": None,
                "data_fresh": False,
                "data_age_seconds": None,
                "timeframe": period,
                "candles": [],
                "analysis": {
                    "signal": "WAIT",
                    "reasons": [
                        "No verified data provider is configured for this selection"
                    ],
                },
                "error": None,
            })
            return

        try:
            candles = build_candles(period)

            tick_time = current["tick_time"]
            now = time.time()

            data_age = (
                now - tick_time
                if tick_time is not None
                else None
            )

            fresh = (
                current["connected"] is True
                and data_age is not None
                and 0 <= data_age <= DATA_MAX_AGE
            )

            analysis = analyze(candles, fresh=fresh)

            if not fresh:
                analysis["signal"] = "WAIT"

            self.send_json({
                "connected": current["connected"],
                "status": current["status"],
                "platform": current["platform"],
                "market_type": current["market_type"],
                "asset": current["asset"],
                "price": current["price"] if fresh else None,
                "tick_time": tick_time,
                "data_age_seconds": data_age,
                "data_fresh": fresh,
                "timeframe": period,
                "candles": candles if fresh else [],
                "analysis": analysis,
                "error": current["error"],
            })

        except Exception:
            logger.exception("Market API failed")
            self.send_json(
                {"error": "Market analysis unavailable"},
                500,
            )

    def log_message(self, format, *args):
        logger.info("HTTP %s - %s", self.address_string(), args)


# ==================================================
# QUOTEX CREDENTIALS
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

                if line and "=" in line and not line.startswith("#"):
                    key, value = line.split("=", 1)

                    os.environ.setdefault(
                        key.strip(),
                        value.strip(),
                    )

    return (
        os.getenv("QUOTEX_EMAIL"),
        os.getenv("QUOTEX_PASSWORD"),
    )


# ==================================================
# QUOTEX WORKER
# ==================================================

async def quotex_worker():
    email, password = load_credentials()

    if not email or not password:
        set_status(
            "Credentials missing",
            "Configure QUOTEX_EMAIL and QUOTEX_PASSWORD",
            False,
        )
        logger.error("Quotex credentials missing")

    delay = RECONNECT_DELAY

    while True:
        platform, market_type, asset = get_selection()

        # Do not connect to unverified providers or OTC feeds.
        if platform != "quotex" or market_type != "forex":
            set_status("UNAVAILABLE", None, False)
            await asyncio.sleep(2)
            continue

        if not email or not password:
            set_status(
                "Credentials missing",
                "Configure QUOTEX_EMAIL and QUOTEX_PASSWORD",
                False,
            )
            await asyncio.sleep(10)
            continue

        client = None
        selection_changed = False

        try:
            set_status("Connecting", None, False)

            client = Quotex(
                email=email,
                password=password,
                lang="en",
            )

            if PRACTICE_ACCOUNT:
                client.set_account_mode("PRACTICE")

            result = await client.connect()

            if not result or not result[0]:
                raise ConnectionError("Quotex connection unsuccessful")

            if get_selection() != (platform, market_type, asset):
                selection_changed = True
                continue

            with lock:
                state["connected"] = True
                state["status"] = "Connected"
                state["error"] = None

            logger.info("Quotex connected for %s", asset)
            delay = RECONNECT_DELAY

            try:
                history = await client.get_historical_candles(
                    asset=asset,
                    amount_of_seconds=7200,
                    period=60,
                    timeout=30,
                    max_workers=1,
                )

                clean_history = []

                for candle in history:
                    try:
                        clean_history.append({
                            "time": int(candle["time"]),
                            "open": safe_float(candle["open"]),
                            "high": safe_float(candle["high"]),
                            "low": safe_float(candle["low"]),
                            "close": safe_float(candle["close"]),
                        })
                    except (ValueError, TypeError, KeyError):
                        continue

                clean_history.sort(key=lambda item: item["time"])

                with lock:
                    historical_candles.clear()
                    historical_candles.extend(clean_history)

                logger.info(
                    "Historical candles loaded: %s",
                    len(clean_history),
                )

            except Exception:
                logger.exception("Historical candle loading failed")

            await client.start_realtime_price(asset, 60)

            while True:
                if get_selection() != (platform, market_type, asset):
                    selection_changed = True
                    break

                prices = await client.get_realtime_price(asset)

                if prices:
                    clean_ticks = []

                    for tick in prices:
                        try:
                            clean_ticks.append({
                                "time": safe_float(tick["time"]),
                                "price": safe_float(tick["price"]),
                            })
                        except (ValueError, TypeError, KeyError):
                            continue

                    clean_ticks.sort(key=lambda item: item["time"])

                    if clean_ticks:
                        with lock:
                            for item in clean_ticks:
                                if (
                                    not ticks
                                    or ticks[-1]["time"] != item["time"]
                                    or ticks[-1]["price"] != item["price"]
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

        except Exception:
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
                    logger.warning("Quotex client close failed")

        if selection_changed:
            set_status("Changing market", None, False)
            continue

        logger.info("Retrying Quotex in %s seconds", delay)
        await asyncio.sleep(delay)
        delay = min(delay * 2, MAX_RECONNECT_DELAY)


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
