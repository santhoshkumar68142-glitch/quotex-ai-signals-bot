from pathlib import Path
import re

FILE = Path.home() / "pyquotex/telegram_app/web/index.html"

html = FILE.read_text()

# Connection status
html = html.replace(
    "● DEMO MODE",
    "● CONNECTING"
)

html = html.replace(
    "● DEMO",
    "● LIVE"
)

html = html.replace(
    "SIMULATED DATA — NOT CONNECTED TO QUOTEX",
    "LIVE MARKET DATA · EDUCATIONAL ANALYSIS"
)

# JavaScript variables
html = re.sub(
    r'let candles = \[\];.*?let candleStart = Date\.now\(\);',
    '''let candles = [];
let currentPrice = 0;
let timeframe = 1;
let candleStart = Date.now();
let marketData = null;
let apiBusy = false;
const API_BASE = "http://127.0.0.1:8000";''',
    html,
    count=1,
    flags=re.S
)

# Remove random candle generator
html = re.sub(
    r'function createCandle\(price\).*?function drawChart\(\)',
    'function drawChart()',
    html,
    count=1,
    flags=re.S
)

# Replace random price update and candle functions
html = re.sub(
    r'function updatePrice\(\).*?function updateTimer\(\)',
    '''async function fetchMarket() {

    if (apiBusy) return;
    apiBusy = true;

    try {
        const response = await fetch(
            API_BASE + "/api/market?tf=" + (timeframe * 60),
            { cache: "no-store" }
        );

        if (!response.ok) {
            throw new Error("API HTTP " + response.status);
        }

        const data = await response.json();
        marketData = data;

        if (data.candles && data.candles.length) {
            candles = data.candles.map(c => ({
                time: c.time,
                open: Number(c.open),
                high: Number(c.high),
                low: Number(c.low),
                close: Number(c.close)
            }));

            candles = candles.slice(-100);
        }

        currentPrice = Number(data.price);

        document.getElementById("price").innerText =
            currentPrice.toFixed(5);

        document.getElementById("change").innerText =
            data.connected ? "● LIVE QUOTEX DATA" : "● DISCONNECTED";

        const status = document.getElementById("connectionStatus");

        if (status) {
            status.innerText = data.connected ? "● LIVE" : "● DISCONNECTED";
            status.style.color = data.connected ? "#00ff9d" : "#ff496c";
        }

        const chartStatus = document.getElementById("chartStatus");

        if (chartStatus) {
            chartStatus.innerText = data.connected ? "● LIVE" : "● OFFLINE";
        }

        renderAnalysis(data.analysis || {});
        drawChart();

    } catch (error) {

        console.error("Market API error:", error);

        const status = document.getElementById("connectionStatus");

        if (status) {
            status.innerText = "● OFFLINE";
            status.style.color = "#ff496c";
        }

        document.getElementById("change").innerText =
            "Backend connection unavailable";

    } finally {
        apiBusy = false;
    }
}

function renderAnalysis(analysis) {

    const rsi = Number(analysis.rsi || 0);
    const ema9 = Number(analysis.ema9 || 0);
    const ema21 = Number(analysis.ema21 || 0);
    const signal = analysis.signal || "WAIT";

    document.getElementById("rsi").innerText =
        rsi.toFixed(1);

    const bullish = ema9 > ema21;

    document.getElementById("trend").innerText =
        bullish ? "BULLISH ↑" : "BEARISH ↓";

    document.getElementById("trend").className =
        "indicator-value " + (bullish ? "green" : "red");

    document.getElementById("momentum").innerText =
        Math.abs(ema9 - ema21) > 0.0001 ? "STRONG" : "NORMAL";

    document.getElementById("market").innerText =
        marketData && marketData.connected ? "LIVE" : "OFFLINE";

    const signalElement = document.getElementById("signal");

    signalElement.innerText = signal;

    signalElement.className = "signal " +
        (signal === "BUY" ? "buy" :
        signal === "SELL" ? "sell" : "wait");

    const reasons = analysis.reasons || [];

    document.getElementById("confidence").innerText =
        reasons.length ? reasons.join(" · ") : "Insufficient market data";
}

function analyze() {

    if (marketData && marketData.analysis) {
        renderAnalysis(marketData.analysis);
    } else {
        fetchMarket();
    }
}

function updateTimer()''',
    html,
    count=1,
    flags=re.S
)

# Timeframe buttons
html = html.replace(
    '        updateTimer();',
    '''        candles = [];
        marketData = null;
        fetchMarket();
        updateTimer();''',
    1
)

# Asset selector
html = re.sub(
    r'document\.getElementById\("asset"\)\.addEventListener\("change",.*?\n\}\);',
    '''document.getElementById("asset").addEventListener("change", () => {
    fetchMarket();
});''',
    html,
    count=1,
    flags=re.S
)

# Remove random update intervals
html = html.replace(
    'setInterval(updatePrice, 1000);',
    'setInterval(fetchMarket, 3000);'
)

html = html.replace(
    'setInterval(newCandle, 5000);',
    ''
)

# Initial startup
html = html.replace(
    'resizeCanvas();\nanalyze();\nupdateTimer();',
    'resizeCanvas();\nfetchMarket();\nupdateTimer();'
)

# Safety checks
if "function fetchMarket()" not in html:
    raise RuntimeError("fetchMarket missing")

if "Math.random()" in html:
    raise RuntimeError("Random simulation code still exists")

FILE.write_text(html)

print("Dashboard integration updated successfully.")
