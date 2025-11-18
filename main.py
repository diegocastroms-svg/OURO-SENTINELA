import os, asyncio, aiohttp, time, threading, statistics, csv
from datetime import datetime, timedelta
from flask import Flask

# =========================
# CONFIGURAÇÃO (versão equilibrada)
# =========================
BINANCE = "https://api.binance.com"
TOP_N = int(os.getenv("TOP_N", "30"))
SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL", "180"))
COOLDOWN_MIN = int(os.getenv("COOLDOWN_MIN", "90"))
MIN_QV_USDT = float(os.getenv("MIN_QV_USDT", "15000000"))
MIN_FORCE = int(os.getenv("MIN_FORCE", "90"))
BOOK_MIN_BUY = float(os.getenv("BOOK_MIN_BUY", "1.55"))
BOOK_MIN_SELL = float(os.getenv("BOOK_MIN_SELL", "0.65"))

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()
PAIRS = os.getenv("PAIRS", "").strip()

# =========================
# APP WEB (Render health)
# =========================
app = Flask(__name__)

@app.route("/")
def home():
    return "OURO-TENDÊNCIA v1.2 – Equilibrado", 200

@app.route("/health")
def health():
    return "OK", 200

# =========================
# UTILITÁRIOS
# =========================
def br_time():
    return datetime.now().strftime("%H:%M:%S")

async def get_json(session, url, params=None, timeout=15):
    for _ in range(2):
        try:
            async with session.get(url, params=params, timeout=timeout) as r:
                return await r.json()
        except:
            await asyncio.sleep(0.4)
    return None

async def fetch_24hr(session):
    return await get_json(session, f"{BINANCE}/api/v3/ticker/24hr")

async def fetch_klines(session, sym, interval="5m", limit=120):
    return await get_json(session, f"{BINANCE}/api/v3/klines",
                          {"symbol": sym, "interval": interval, "limit": limit})

async def fetch_depth(session, sym, limit=40):
    return await get_json(session, f"{BINANCE}/api/v3/depth",
                          {"symbol": sym, "limit": limit})

# =========================
# INDICADORES
# =========================
def ema(values, period):
    k = 2 / (period + 1)
    e = values[0]
    for v in values[1:]:
        e = v * k + e * (1 - k)
    return e

def macd(values, fast=12, slow=26, signal=9):
    if len(values) < slow + signal:
        return 0, 0
    ema_fast = ema(values[-(slow + signal):], fast)
    ema_slow = ema(values[-(slow + signal):], slow)
    macd_line = ema_fast - ema_slow
    sig = ema([macd_line] * signal, signal)
    return macd_line, macd_line - sig

def rsi(values, period=14):
    if len(values) < period + 1:
        return 50
    gains = [max(values[i] - values[i-1], 0) for i in range(1, len(values))]
    losses = [max(values[i-1] - values[i], 0) for i in range(1, len(values))]
    ag = sum(gains[-period:]) / period
    al = sum(losses[-period:]) / period or 1e-9
    return 100 - (100 / (1 + ag / al))

def book_ratio(depth):
    def power(levels):
        return sum(float(p) * float(q) for p, q in levels[:20])
    b = power(depth.get("bids", []))
    a = power(depth.get("asks", []))
    return b / a if a > 0 else 0

def bollinger_width(closes, period=20):
    if len(closes) < period:
        return 0
    window = closes[-period:]
    mean = sum(window) / period
    std = (sum((c - mean)**2 for c in window) / period) ** 0.5
    up = mean + std * 2
    dn = mean - std * 2
    return ((up - dn) / mean) * 100 if mean != 0 else 0

# =========================
# LOOP PRINCIPAL
# =========================
cooldown = {}

async def scan_once():
    async with aiohttp.ClientSession() as s:

        all24 = await fetch_24hr(s)
        if not all24:
            return

        allow = set(PAIRS.split(",")) if PAIRS else None

        pool = [(x["symbol"], float(x["quoteVolume"])) for x in all24
                if x["symbol"].endswith("USDT")
                and (not allow or x["symbol"] in allow)
                and float(x["quoteVolume"]) >= MIN_QV_USDT]

        symbols = [s for s, _ in sorted(pool, key=lambda t: t[1], reverse=True)[:TOP_N]]

        for sym in symbols:
            try:
                kl, dp = await asyncio.gather(
                    fetch_klines(s, sym, "5m", 120),
                    fetch_depth(s, sym, 40)
                )

                if not kl or not dp:
                    continue

                closes = [float(k[4]) for k in kl]
                vols = [float(k[5]) for k in kl]

                ema9 = ema(closes[-40:], 9)
                ema20 = ema(closes[-40:], 20)
                ema200 = ema(closes, 200)

                macd_line, hist = macd(closes)
                rsi_val = rsi(closes)

                base = statistics.median(vols[-11:-1]) or 1
                vol_mult = vols[-1] / base

                ratio = book_ratio(dp)
                bw = bollinger_width(closes)

                row = [
                    br_time(),
                    sym,
                    closes[-1],
                    rsi_val,
                    macd_line,
                    hist,
                    ema9,
                    ema20,
                    ema200,
                    vols[-1],
                    vol_mult,
                    ratio,
                    bw
                ]

                with open("dados_coletados.csv", "a", newline="") as f:
                    writer = csv.writer(f)
                    writer.writerow(row)

                print(f"[{br_time()}] COLETADO: {sym}")

                await asyncio.sleep(0.05)

            except Exception as e:
                print("Erro:", e)

async def monitor_loop():
    print("🚀 OURO-TENDÊNCIA v1.2 – COLETANDO PADRÕES (ativo).")
    while True:
        try:
            await scan_once()
            await asyncio.sleep(SCAN_INTERVAL)
        except Exception as e:
            print("⚠️ Erro:", e)
            await asyncio.sleep(5)

if __name__ == "__main__":
    threading.Thread(
        target=lambda: app.run(host="0.0.0.0",
        port=int(os.getenv("PORT", 10000)))
    ).start()
    time.sleep(2)
    asyncio.run(monitor_loop())
