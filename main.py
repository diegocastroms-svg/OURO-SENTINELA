import os, asyncio, aiohttp, time, threading
from datetime import datetime, timedelta
from flask import Flask

BINANCE = "https://api.binance.com"

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()

SCAN_INTERVAL = 30
MIN_QV_USDT = 10_000_000

COOLDOWN_15M = 14400
COOLDOWN_1D = 14400

app = Flask(__name__)

@app.route("/")
def home():
    return "SENTINELA TREND-VOLUME ATIVO", 200

def now():
    agora_brasilia = datetime.now() - timedelta(hours=3)
    return agora_brasilia.strftime("%d/%m/%Y %H:%M:%S")

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

def moving_average(values, window):
    if len(values) < window:
        return 0
    return sum(values[-window:]) / window

def calcular_macd(closes):
    ema12 = []
    ema26 = []

    k12 = 2/(12+1)
    k26 = 2/(26+1)

    for i, price in enumerate(closes):
        if i == 0:
            ema12.append(price)
            ema26.append(price)
        else:
            ema12.append(price * k12 + ema12[-1]*(1-k12))
            ema26.append(price * k26 + ema26[-1]*(1-k26))

    macd = [a-b for a,b in zip(ema12, ema26)]

    signal = []
    k9 = 2/(9+1)

    for i, val in enumerate(macd):
        if i == 0:
            signal.append(val)
        else:
            signal.append(val * k9 + signal[-1]*(1-k9))

    hist = [m-s for m,s in zip(macd, signal)]

    return macd, signal, hist

def par_eh_valido(sym):

    base = sym.replace("USDT", "").upper()

    invalid = ("BRL","TRY","GBP","AUD","CAD","CHF","MXN","ZAR","RUB","BKRW","BVND","IDRT",
               "BUSD","TUSD","FDUSD","USDC","USDP","USDE","USDD","USDX","USDJ","PAXG","BFUSD")

    if base in invalid:
        return False

    lixo = ("INU","PEPE","FLOKI","BABY","CAT","DOGE2","SHIB2","MOON","MEME","OLD","NEW",
            "PUP","PUPPY","TURBO","WIF","AI")

    if any(k in base for k in lixo):
        return False

    if sym.endswith(("UPUSDT","DOWNUSDT","BULLUSDT","BEARUSDT")):
        return False

    return True

_last_signal_time = {}

async def analisar_15m(sym, klines):

    closes = [float(k[4]) for k in klines]

    ema9 = moving_average(closes,9)
    ema20 = moving_average(closes,20)
    ema50 = moving_average(closes,50)
    ema200 = moving_average(closes,200)

    macd, signal, hist = calcular_macd(closes)

    macd_atual = hist[-1]
    macd_prev = hist[-2]

    last_close = closes[-1]

    nome = sym.replace("USDT","")
    data_hora_atual = now()

    key = f"{sym}_5M_SETUP"

    now_ts = time.time()

    # LONG
    if (
        last_close > ema200 and
        ema9 > ema20 > ema50 and
        macd_prev < 0 and macd_atual > 0
    ):

        if now_ts - _last_signal_time.get(key,0) > COOLDOWN_15M:

            _last_signal_time[key] = now_ts

            msg = (
                f"🚀 LONG INÍCIO\n\n"
                f"{nome}\n"
                f"EMA alinhadas + tendência\n"
                f"MACD virando verde\n"
                f"Preço: {last_close:.6f}\n"
                f"{data_hora_atual}"
            )

            await send(msg)

    # SHORT
    elif (
        last_close < ema200 and
        ema9 < ema20 < ema50 and
        macd_prev > 0 and macd_atual < 0
    ):

        if now_ts - _last_signal_time.get(key,0) > COOLDOWN_15M:

            _last_signal_time[key] = now_ts

            msg = (
                f"🔻 SHORT INÍCIO\n\n"
                f"{nome}\n"
                f"EMA alinhadas + tendência\n"
                f"MACD virando vermelho\n"
                f"Preço: {last_close:.6f}\n"
                f"{data_hora_atual}"
            )

            await send(msg)

async def analisar_1d(sym, klines):
    return

async def monitor_loop():

    await send(f"SENTINELA ATIVO EM: {now()}")

    while True:

        try:

            async with aiohttp.ClientSession() as s:

                data24 = await get_json(s, f"{BINANCE}/api/v3/ticker/24hr")

                if not data24:
                    await asyncio.sleep(5)
                    continue

                for x in data24:

                    sym = x["symbol"]

                    if not sym.endswith("USDT"):
                        continue

                    if not par_eh_valido(sym):
                        continue

                    vol24 = float(x.get("quoteVolume",0))

                    if 5_000_000 <= vol24 <= 100_000_000:

                        kl_15m = await get_json(
                            s,
                            f"{BINANCE}/api/v3/klines",
                            {"symbol":sym,"interval":"5m","limit":210}
                        )

                        if kl_15m:
                            await analisar_15m(sym, kl_15m)

                    await asyncio.sleep(0.05)

            await asyncio.sleep(SCAN_INTERVAL)

        except Exception as e:

            print(f"Erro no loop: {e}")
            await asyncio.sleep(10)

def start_bot():

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(monitor_loop())

if __name__ == "__main__":

    threading.Thread(target=start_bot, daemon=True).start()

    port = int(os.getenv("PORT",10000))

    app.run(host="0.0.0.0", port=port)
