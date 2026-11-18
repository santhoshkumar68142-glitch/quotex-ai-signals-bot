import os
import asyncio
import urllib.parse
import urllib.request
import json

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes
)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
API_KEY = os.getenv("TWELVE_DATA_API_KEY")

ALLOWED_IDS = os.getenv("ALLOWED_TELEGRAM_IDS", "")


def is_allowed(user_id):
    if not ALLOWED_IDS:
        return True

    allowed = [x.strip() for x in ALLOWED_IDS.split(",")]
    return str(user_id) in allowed


def get_market_data(symbol, interval):
    params = urllib.parse.urlencode({
        "symbol": symbol,
        "interval": interval,
        "outputsize": 100,
        "apikey": API_KEY
    })

    url = f"https://api.twelvedata.com/time_series?{params}"

    req = urllib.request.Request(
        url,
        headers={"User-Agent": "TradingSignalsBot"}
    )

    with urllib.request.urlopen(req, timeout=20) as response:
        data = json.loads(response.read().decode())

    if "values" not in data:
        raise Exception(data.get("message", "Market data unavailable"))

    return list(reversed(data["values"]))


def ema(values, period):
    multiplier = 2 / (period + 1)

    result = [sum(values[:period]) / period]

    for price in values[period:]:
        result.append(
            (price * multiplier) +
            (result[-1] * (1 - multiplier))
        )

    return result


def calculate_rsi(values, period=14):
    changes = [
        values[i] - values[i - 1]
        for i in range(1, len(values))
    ]

    gains = [max(x, 0) for x in changes]
    losses = [max(-x, 0) for x in changes]

    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period

    if avg_loss == 0:
        return 100

    rs = avg_gain / avg_loss

    return 100 - (100 / (1 + rs))


def analyze_market(candles):
    closes = [float(x["close"]) for x in candles]
    highs = [float(x["high"]) for x in candles]
    lows = [float(x["low"]) for x in candles]

    current = closes[-1]

    ema9 = ema(closes, 9)[-1]
    ema21 = ema(closes, 21)[-1]

    rsi = calculate_rsi(closes)

    macd_line = ema(closes, 12)[-1] - ema(closes, 26)[-1]

    macd_signal = ema(
        [
            ema(closes, 12)[i] - ema(closes, 26)[i]
            for i in range(25, len(closes))
        ],
        9
    )[-1]

    support = min(lows[-20:])
    resistance = max(highs[-20:])

    score = 0
    reasons = []

    if ema9 > ema21:
        score += 1
        reasons.append("EMA9 above EMA21")

    elif ema9 < ema21:
        score -= 1
        reasons.append("EMA9 below EMA21")

    if current > ema9:
        score += 1

    elif current < ema9:
        score -= 1

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

    confidence = min(abs(score) / 4 * 100, 100)

    return {
        "price": current,
        "ema9": ema9,
        "ema21": ema21,
        "rsi": rsi,
        "macd": macd_line,
        "support": support,
        "resistance": resistance,
        "signal": signal,
        "confidence": confidence,
        "reasons": reasons
    }


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not is_allowed(update.effective_user.id):
        await update.message.reply_text("⛔ Unauthorized access.")
        return

    keyboard = [
        [
            InlineKeyboardButton(
                "📊 Analyze EUR/USD",
                callback_data="signal"
            )
        ],
        [
            InlineKeyboardButton(
                "📚 Free Trading Academy",
                url="https://tradingstrategycourse.com/courses-archive/"
            )
        ]
    ]

    await update.message.reply_text(
        "🤖 AI Trading Signals Bot\n\n"
        "Forex market analysis powered by technical indicators.\n\n"
        "Commands:\n"
        "/signal EUR/USD 5min\n"
        "/signal GBP/USD 15min\n"
        "/markets\n"
        "/help\n\n"
        "⚠️ Educational analysis only. "
        "Trading involves risk.",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def signal(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not is_allowed(update.effective_user.id):
        await update.message.reply_text("⛔ Unauthorized access.")
        return

    symbol = "EUR/USD"
    interval = "5min"

    if len(context.args) >= 1:
        symbol = context.args[0].upper()

    if len(context.args) >= 2:
        interval = context.args[1].lower()

    valid_intervals = {
        "1min", "5min", "15min", "30min",
        "1h", "4h", "1day"
    }

    if interval not in valid_intervals:
        await update.message.reply_text(
            "Invalid timeframe.\n"
            "Available: 1min, 5min, 15min, 30min, 1h, 4h, 1day"
        )
        return

    await update.message.reply_text(
        f"🔍 Analyzing {symbol} ({interval})..."
    )

    try:
        candles = await asyncio.to_thread(
            get_market_data,
            symbol,
            interval
        )

        analysis = analyze_market(candles)

        message = (
            f"📊 MARKET ANALYSIS\n\n"
            f"Pair: {symbol}\n"
            f"Timeframe: {interval}\n"
            f"Price: {analysis['price']}\n\n"
            f"━━━━━━━━━━━━━━\n"
            f"📈 INDICATORS\n"
            f"EMA 9: {analysis['ema9']:.5f}\n"
            f"EMA 21: {analysis['ema21']:.5f}\n"
            f"RSI 14: {analysis['rsi']:.2f}\n"
            f"MACD: {analysis['macd']:.6f}\n\n"
            f"Support: {analysis['support']:.5f}\n"
            f"Resistance: {analysis['resistance']:.5f}\n\n"
            f"━━━━━━━━━━━━━━\n"
            f"Signal: {analysis['signal']}\n"
            f"Score: {analysis['confidence']:.0f}%\n\n"
            f"Reasons:\n"
        )

        for reason in analysis["reasons"]:
            message += f"• {reason}\n"

        message += (
            "\n⚠️ Educational analysis only.\n"
            "Signals are not guaranteed to be profitable."
        )

        await update.message.reply_text(message)

    except Exception as e:
        await update.message.reply_text(
            f"❌ Market data error:\n{str(e)}"
        )


async def markets(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not is_allowed(update.effective_user.id):
        return

    await update.message.reply_text(
        "📊 Supported Forex symbols:\n\n"
        "EUR/USD\n"
        "GBP/USD\n"
        "USD/JPY\n"
        "AUD/USD\n"
        "USD/CAD\n"
        "USD/CHF\n"
        "NZD/USD\n"
        "EUR/JPY\n"
        "GBP/JPY\n"
        "EUR/GBP\n\n"
        "Example:\n"
        "/signal EUR/USD 5min"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not is_allowed(update.effective_user.id):
        return

    await update.message.reply_text(
        "🤖 TRADING BOT COMMANDS\n\n"
        "/start - Start bot\n"
        "/signal EUR/USD 5min - Analyze market\n"
        "/signal GBP/USD 15min - Analyze market\n"
        "/markets - Supported markets\n"
        "/help - Commands\n\n"
        "Timeframes:\n"
        "1min, 5min, 15min, 30min, 1h, 4h, 1day"
    )


def main():

    if not BOT_TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN missing")

    if not API_KEY:
        raise ValueError("TWELVE_DATA_API_KEY missing")

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("signal", signal))
    app.add_handler(CommandHandler("markets", markets))
    app.add_handler(CommandHandler("help", help_command))

    print("AI Trading Signals Bot Started")

    app.run_polling()


if __name__ == "__main__":
    main()
