
# Bot 2 – AI Trading Signals & Market Analysis

An educational Telegram trading bot and web dashboard for Forex and cryptocurrency market analysis.

## Features

- Telegram bot for market analysis
- Forex and cryptocurrency market data through Twelve Data
- EMA, RSI and MACD indicators
- BUY, SELL and WAIT analysis suggestions
- Support and resistance levels
- Interactive web dashboard
- Live candle monitoring through the Quotex integration
- Educational ICT and Smart Money Concepts (SMC) analysis – planned enhancements

## Supported Markets

- Regular Forex markets
- Cryptocurrency markets
- Quotex OTC markets (subject to supported data availability)

**Important:** Quotex OTC and regular Forex are different price feeds. They must not be treated as identical or directly cross-checked.

## Project Structure

```text
.
├── backend/
│   ├── __init__.py
│   └── server.py
├── web/
│   └── index.html
├── bot.py
├── patch_pyquotex.py
├── connect_dashboard.py
├── requirements.txt
└── .gitignore
```

## Installation

1. Clone this repository:

```bash
git clone https://github.com/santhoshkumar68142-glitch/quotex-ai-signals-bot.git
cd quotex-ai-signals-bot
```

2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Configure your API credentials using environment variables. Never publish API keys, Telegram tokens or Quotex session credentials.

4. Start the components using the appropriate configuration for your deployment environment.

## Data Sources

- Twelve Data for supported market data.
- Quotex integration through the PyQuotex library, subject to compatibility and platform availability.

Data availability depends on provider plans, API limits and supported instruments.

## Disclaimer

This project is for educational and informational purposes only. It does not provide guaranteed trading results or personalized financial advice.

Trading Forex, cryptocurrency, binary options and other leveraged products involves substantial risk. Signals and indicators can be inaccurate. Always perform your own research and use demo accounts for testing before considering real-money trading.

This project does not guarantee profits and does not automatically execute trades.
