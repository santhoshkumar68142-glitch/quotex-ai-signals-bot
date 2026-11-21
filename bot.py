
import os
import re
import json
import time
import asyncio
import logging
import urllib.parse
import urllib.request
import urllib.error

from collections import defaultdict

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

# ---------------- CONFIGURATION ----------------

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
API_KEY = os.getenv("TWELVE_DATA_API_KEY")

ALLOWED_IDS = os.getenv("ALLOWED_TELEGRAM_IDS", "")

API_BASE = "https://api.twelvedata.com/time_series"

VALID_INTERVALS = {
    "1min", "5min", "15min", "30min",
    "1h", "4h", "1day"
}

VALID_SYMBOLS = {
    "EUR/USD", "GBP/USD", "USD/JPY",
    "AUD/USD", "USD/CAD", "USD/CHF",
    "NZD/USD", "EUR/JPY", "GBP/JPY",
    "EUR/GBP", "BTC/USD",
}

CACHE_SECONDS = 30
USER_COOLDOWN = 10

cache = {}
last_request = defaultdict(float)

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)


# ---------------- ACCESS CONTROL ----------------

def is_allowed(user_id):
    if not ALLOWED_IDS.strip():
        return True

    allowed = {
        item.strip()
        for item in ALLOWED_IDS.split(",")
        if item.strip()
    }

    return str(user_id) in allowed


def valid_symbol(symbol):
    return symbol in VALID_SYMBOLS


def valid_interval(interval):
    return interval in VALID_INTERVALS


def check_cooldown(user_id):
    now = time.monotonic()

    if now - last_request[user_id] < USER_COOLDOWN:
        return False

    last_request[user_id] = now
    return True


# ---------------- MARKET DATA ----------------

def get_market_data(symbol, interval):

    cache_key = (symbol, interval)
    now = time.monotonic()

    cached = cache.get(cache_key)

    if cached and now - cached["time"] < CACHE_SECONDS:
        return cached["data"]

    params = urllib.parse.urlencode({
        "symbol": symbol,
        "interval": interval,
        "outputsize": 100,
        "apikey": API_KEY,
    })

    request = urllib.request.Request(
        f"{API_BASE}?{params}",
        headers={"User-Agent": "Bot2-TradingAnalysis"},
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=20,
        ) as response:

            data = json.loads(
                response.read().decode("utf-8")
            )

    except urllib.error.HTTPError as error:
        logger.warning("Market API HTTP error: %s", error.code)
        raise RuntimeError("Market data provider temporarily unavailable.")

    except urllib.error.URLError:
        raise RuntimeError("Unable to connect to market data provider.")

    except TimeoutError:
        raise RuntimeError("Market data request timed out.")

    if "values" not in data:
        logger.warning(
            "Market API response: %s",
            data.get("message", "Unknown provider response"),
        )
        raise RuntimeError("Market data unavailable. Please try again later.")

    values = data["values"]

    if not isinstance(values, list) or len(values) < 30:
        raise RuntimeError("Not enough candle data for reliable analysis.")

    candles = list(reversed(values))

    for candle in candles:
        for field in ("open", "high", "low", "close"):
            try:
                value = float(candle[field])
                if not value == value:
                    raise ValueError()
            except (KeyError, TypeError, ValueError):
                raise RuntimeError("Invalid candle data received.")

    cache[cache_key] = {
        "time": now,
        "data": candles,
    }

    return candles


# ---------------- INDICATORS ----------------

def ema(values, period):

    if len(values) < period:
        raise ValueError("Not enough values for EMA.")

    multiplier = 2 / (period + 1)

    result = [sum(values[:period]) / period]

    for price in values[period:]:
        result.append(
            price * multiplier +
            result[-1] * (1 - multiplier)
        )

    return result


def calculate_rsi(values, period=14):

    if len(values) <= period:
        raise ValueError("Not enough values for RSI.")

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


# ---------------- MARKET ANALYSIS ----------------

def analyze_market(candles):

    if len(candles) < 30:
        raise ValueError("Insufficient candle history.")

    closes = [float(candle["close"]) for candle in candles]
    highs = [float(candle["high"]) for candle in candles]
    lows = [float(candle["low"]) for candle in candles]

    current = closes[-1]

    ema9 = ema(closes, 9)[-1]
    ema21 = ema(closes, 21)[-1]

    rsi = calculate_rsi(closes)

    ema12 = ema(closes, 12)
    ema26 = ema(closes, 26)

    macd_values = [
        ema12[i] - ema26[i]
        for i in range(len(ema26))
    ]

    macd_line = macd_values[-1]
    macd_signal = ema(macd_values, 9)[-1]

    support = min(lows[-20:])
    resistance = max(highs[-20:])

    score = 0
    reasons = []

    if ema9 > ema21:
        score += 1
        reasons.append("EMA 9 above EMA 21")

    elif ema9 < ema21:
        score -= 1
        reasons.append("EMA 9 below EMA 21")

    if current > ema9:
        score += 1
        reasons.append("Price above EMA 9")

    elif current < ema9:
        score -= 1
        reasons.append("Price below EMA 9")

    if rsi > 50:
        score += 1
        reasons.append("RSI bullish")

    elif rsi < 50:
        score -= 1
        reasons.append("RSI bearish")

    if macd_line > macd_signal:
        score += 1
        reasons.append("MACD bullish")

    elif macd_line < macd_signal:
        score -= 1
        reasons.append("MACD bearish")

    if score >= 3:
        signal = "🟢 BUY"
    elif score <= -3:
        signal = "🔴 SELL"
    else:
        signal = "🟡 WAIT"

    # This is indicator agreement, NOT win probability.
    agreement = abs(score) / 4 * 100

    return {
        "price": current,
        "ema9": ema9,
        "ema21": ema21,
        "rsi": rsi,
        "macd": macd_line,
        "macd_signal": macd_signal,
        "support": support,
        "resistance": resistance,
        "signal": signal,
        "agreement": agreement,
        "reasons": reasons,
    }


# ---------------- SIGNAL MESSAGE ----------------

def build_signal_message(symbol, interval, analysis):

    reasons = "\n".join(
        f"• {reason}"
        for reason in analysis["reasons"]
    )

    return (
        "📊 MARKET ANALYSIS\n\n"
        f"Pair: {symbol}\n"
        f"Timeframe: {interval}\n"
        f"Price: {analysis['price']}\n\n"
        "━━━━━━━━━━━━━━\n"
        "📈 INDICATORS\n"
        f"EMA 9: {analysis['ema9']:.5f}\n"
        f"EMA 21: {analysis['ema21']:.5f}\n"
        f"RSI 14: {analysis['rsi']:.2f}\n"
        f"MACD: {analysis['macd']:.6f}\n\n"
        f"Support: {analysis['support']:.5f}\n"
        f"Resistance: {analysis['resistance']:.5f}\n\n"
        "━━━━━━━━━━━━━━\n"
        f"Signal: {analysis['signal']}\n"
        f"Indicator agreement: {analysis['agreement']:.0f}%\n\n"
        "Reasons:\n"
        f"{reasons or 'No strong indicator agreement.'}\n\n"
        "⚠️ Indicator agreement is NOT a win probability.\n"
        "Educational analysis only. Trading involves risk."
    )


# ---------------- SIGNAL COMMAND ----------------

async def run_signal(update, symbol, interval):

    if not valid_symbol(symbol):
        await update.effective_message.reply_text(
            "Unsupported symbol.\nUse /markets to see available symbols."
        )
        return

    if not valid_interval(interval):
        await update.effective_message.reply_text(
            "Invalid timeframe.\n"
            "Available: 1min, 5min, 15min, 30min, 1h, 4h, 1day"
        )
        return

    await update.effective_message.reply_text(
        f"🔍 Analyzing {symbol} ({interval})..."
    )

    try:
        candles = await asyncio.to_thread(
            get_market_data,
            symbol,
            interval,
        )

        analysis = analyze_market(candles)

        await update.effective_message.reply_text(
            build_signal_message(symbol, interval, analysis)
        )

    except Exception:
        logger.exception("Signal analysis failed")
        await update.effective_message.reply_text(
            "❌ Analysis unavailable right now. Please try again later."
        )


async def signal(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user = update.effective_user

    if not user or not is_allowed(user.id):
        await update.effective_message.reply_text(
            "⛔ Unauthorized access."
        )
        return

    if not check_cooldown(user.id):
        await update.effective_message.reply_text(
            "Please wait a few seconds before requesting another analysis."
        )
        return

    symbol = "EUR/USD"
    interval = "5min"

    if len(context.args) >= 1:
        symbol = context.args[0].upper()

    if len(context.args) >= 2:
        interval = context.args[1].lower()

    await run_signal(update, symbol, interval)


# ---------------- BUTTON CALLBACK ----------------

async def signal_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    if not is_allowed(query.from_user.id):
        await query.answer("Unauthorized", show_alert=True)
        return

    if not check_cooldown(query.from_user.id):
        await query.answer(
            "Please wait before requesting another analysis.",
            show_alert=True,
        )
        return

    await query.answer()

    await run_signal(
        update,
        "EUR/USD",
        "5min",
    )


# ---------------- START COMMAND ----------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user = update.effective_user

    if not user or not is_allowed(user.id):
        await update.effective_message.reply_text(
            "⛔ Unauthorized access."
        )
        return

    keyboard = [
        [
            InlineKeyboardButton(
                "📊 Analyze EUR/USD",
                callback_data="signal",
            )
        ],
        [
            InlineKeyboardButton(
                "📚 Free Trading Academy",
                url="https://tradingstrategycourse.com/courses-archive/",
            )
        ],
    ]

    await update.effective_message.reply_text(
        "🤖 BOT 2 - AI MARKET ANALYSIS\n\n"
        "Forex analysis powered by technical indicators.\n\n"
        "Commands:\n"
        "/signal EUR/USD 5min\n"
        "/signal GBP/USD 15min\n"
        "/markets\n"
        "/help\n\n"
        "⚠️ Educational analysis only. Trading involves risk.",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


# ---------------- MARKETS COMMAND ----------------

async def markets(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not is_allowed(update.effective_user.id):
        return

    await update.effective_message.reply_text(
        "📊 Supported symbols:\n\n"
        "EUR/USD\n"
        "GBP/USD\n"
        "USD/JPY\n"
        "AUD/USD\n"
        "USD/CAD\n"
        "USD/CHF\n"
        "NZD/USD\n"
        "EUR/JPY\n"
        "GBP/JPY\n"
        "EUR/GBP\n"
        "BTC/USD\n\n"
        "Example:\n"
        "/signal EUR/USD 5min"
    )


# ---------------- HELP COMMAND ----------------

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not is_allowed(update.effective_user.id):
        return

    await update.effective_message.reply_text(
        "🤖 BOT 2 COMMANDS\n\n"
        "/start - Start bot\n"
        "/signal EUR/USD 5min - Analyze market\n"
        "/markets - Supported symbols\n"
        "/help - Show commands\n\n"
        "Timeframes:\n"
        "1min, 5min, 15min, 30min, 1h, 4h, 1day"
    )


# ---------------- ERROR HANDLER ----------------

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
):

    logger.error(
        "Telegram application error",
        exc_info=context.error,
    )


# ---------------- MAIN ----------------

def main():

    if not BOT_TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN missing.")

    if not API_KEY:
        raise ValueError("TWELVE_DATA_API_KEY missing.")

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("signal", signal))
    app.add_handler(CommandHandler("markets", markets))
    app.add_handler(CommandHandler("help", help_command))

    app.add_handler(
        CallbackQueryHandler(
            signal_callback,
            pattern="^signal$",
        )
    )

    app.add_error_handler(error_handler)

    logger.info("Bot 2 started.")

    app.run_polling()


if __name__ == "__main__":
    main()
