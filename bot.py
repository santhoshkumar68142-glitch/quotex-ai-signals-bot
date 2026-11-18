import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("📊 Open Market Dashboard", callback_data="dashboard")],
        [InlineKeyboardButton("📚 Free Trading Academy", url="https://tradingstrategycourse.com/courses-archive/")]
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "🚀 Welcome to Quotex AI Signals!\n\n"
        "📊 Market analysis\n"
        "📈 RSI & EMA indicators\n"
        "🕯️ Candlestick analysis\n"
        "🔍 Trend identification\n\n"
        "⚠️ Signals are educational only. No guaranteed profits.\n"
        "Trading involves substantial risk.",
        reply_markup=reply_markup
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Available commands:\n"
        "/start - Start the bot\n"
        "/help - Help information\n\n"
        "This bot provides educational market analysis only."
    )

def main():
    token_path = os.path.expanduser("~/.telegram_bot_token")

    with open(token_path, "r") as f:
        token = f.read().strip()

    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))

    print("Quotex AI Signals Bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()
