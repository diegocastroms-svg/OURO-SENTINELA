import os, asyncio, aiohttp, time, threading
from datetime import datetime
from flask import Flask

BINANCE = "https://api.binance.com"

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()

SCAN_INTERVAL = 30
MIN_QV_USDT = 2_000_000

TF_15M = "15m"
TF_1H = "1h"
TF_4H = "4h"
TF_1D = "1d"

app = Flask(__name__)
@app.route("/")
def home():
    return "SENTINELA BREAKOUT MULTI-TF", 200

@app.route("/health")
def health():
    return "OK", 200

def now():
    return datetime.now().strftime("%H:%M:%S")

async def send(msg):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        return
    try:
        async with aiohttp.ClientSession() as s:
            await s.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                json={"chat_id": CHAT_ID, "text": msg}
            )
    except:
        pass

async def get_json(session, url, params=None):
    try:
        async with session.get(url, params=params, timeout=10) as r:
            return await r.json()
    except:
        return None

async def fetch_24hr(session):
    return await get_json(session, f"{BINANCE}/api/v3/ticker/24hr")

async def fetch_klines(session, sym, interval, limit=50):
    return await get_json(
        session,
        f"{BINANCE}/api/v3/klines",
        {"symbol": sym, "interval": interval, "limit": limit}
    )

def par_eh_valido(sym):
    base = sym.replace("USDT", "").upper()

    invalid = (
        "BRL","TRY","GBP","AUD","CAD","CHF","MXN","ZAR","RUB",
        "BKRW","BVND","IDRT",
        "BUSD","TUSD","FDUSD","USDC","USDP","USDE","USDD",
        "USDX","USDJ","PAXG","BFUSD"
    )

    if base in invalid:
        return False

    lixo = (
        "INU","PEPE","FLOKI","BABY","CAT","DOGE2","SHIB2","MOON",
        "MEME","OLD","NEW","PUP","PUPPY","TURBO","WIF","AI"
    )
    if any(k in base for k in lixo):
        return False

    if sym.endswith(("UPUSDT","DOWNUSDT","BULLUSDT","BEARUSDT")):
        return False

    return True


_last_candle = {}


async def alerta_breakout(session, sym, klines, tf, emoji):

    candle_time = klines[-1][0]

    key = sym + tf

    if _last_candle.get(key) == candle_time:
        return

    _last_candle[key] = candle_time

    closes = [float(k[4]) for k in klines]
    highs  = [float(k[2]) for k in klines]
    lows   = [float(k[3]) for k in klines]
    volumes = [float(k[5]) for k in klines]

    last_close = closes[-1]

    high5 = max(highs[-6:-1])
    low5 = min(lows[-6:-1])

    vol_media = sum(volumes[-10:-1]) / 9
    vol_atual = volumes[-1]

    lateral_range = (high5 - low5) / last_close * 100

    # ===== FILTRO ADAPTATIVO ATR =====
    trs = []
    for i in range(1, len(highs)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i-1]),
            abs(lows[i] - closes[i-1])
        )
        trs.append(tr)

    atr = sum(trs[-14:]) / 14
    atr_percent = (atr / last_close) * 100

    if lateral_range > atr_percent * 0.8:
        return
    # =================================

    nome = sym.replace("USDT", "")

    if vol_atual > vol_media and last_close > high5:

        msg = (
            f"{emoji} BREAKOUT {tf} LONG\n\n"
            f"{nome}\n\n"
            f"Preço: {last_close:.6f}"
        )

        await send(msg)
        print(f"[{now()}] LONG {sym} {tf}")

    if vol_atual > vol_media and last_close < low5:

        msg = (
            f"{emoji} BREAKOUT {tf} SHORT\n\n"
            f"{nome}\n\n"
            f"Preço: {last_close:.6f}"
        )

        await send(msg)
        print(f"[{now()}] SHORT {sym} {tf}")


async def monitor_loop():

    await send("SENTINELA BREAKOUT MULTI-TF INICIADO")
    print("BOT RODANDO...")

    while True:

        try:

            async with aiohttp.ClientSession() as s:

                data24 = await fetch_24hr(s)

                if not data24:
                    await asyncio.sleep(5)
                    continue

                pool = []

                for x in data24:

                    sym = x.get("symbol")
                    vol = x.get("quoteVolume")

                    if not sym or not vol:
                        continue

                    if not sym.endswith("USDT"):
                        continue

                    if not par_eh_valido(sym):
                        continue

                    if float(vol) >= MIN_QV_USDT:
                        pool.append(sym)

                print(f"[{now()}] Monitorando {len(pool)} pares...")

                for sym in pool:

                    kl = await fetch_klines(s, sym, TF_15M)
                    if kl:
                        await alerta_breakout(s, sym, kl, "15M", "🟢")

                    kl = await fetch_klines(s, sym, TF_1H)
                    if kl:
                        await alerta_breakout(s, sym, kl, "1H", "🔵")

                    kl = await fetch_klines(s, sym, TF_4H)
                    if kl:
                        await alerta_breakout(s, sym, kl, "4H", "🟠")

                    kl = await fetch_klines(s, sym, TF_1D)
                    if kl:
                        await alerta_breakout(s, sym, kl, "1D", "🟣")

                await asyncio.sleep(SCAN_INTERVAL)

        except Exception as e:

            print(f"[{now()}] ERRO LOOP: {e}")
            await asyncio.sleep(5)


def start_bot():
    asyncio.run(monitor_loop())

threading.Thread(target=start_bot, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
