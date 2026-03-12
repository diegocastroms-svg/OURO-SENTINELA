import os, asyncio, aiohttp, time, threading
from datetime import datetime, timedelta
from flask import Flask

BINANCE = "https://api.binance.com"

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()

SCAN_INTERVAL = 30
MIN_QV_USDT = 30_000_000

COOLDOWN_1M = 3600
COOLDOWN_1D = 86400

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

async def analisar_1m(sym, klines):

    closes = [float(k[4]) for k in klines]
    volumes = [float(k[5]) for k in klines]

    ma200 = moving_average(closes[:-1], 200)

    last_close = closes[-1]
    prev_close = closes[-2]

    vol_atual = volumes[-1]
    vol_prev1 = volumes[-2]
    vol_prev2 = volumes[-3]

    vol_ok = vol_atual >= (max(vol_prev1, vol_prev2) * 1.5)

    nome = sym.replace("USDT","")
    data_hora_atual = now()

    key = f"{sym}_1M_MA200"

    now_ts = time.time()

    if vol_ok:

        if prev_close < ma200 and last_close > ma200:

            if now_ts - _last_signal_time.get(key,0) > COOLDOWN_1M:

                _last_signal_time[key] = now_ts

                msg = (
                    f"⏱ LONG 1M\n\n"
                    f"{nome}\n"
                    f"Preço cruzou MA200\n"
                    f"Preço: {last_close:.6f}\n"
                    f"Volume 1.5x+\n"
                    f"{data_hora_atual}"
                )

                await send(msg)

        elif prev_close > ma200 and last_close < ma200:

            if now_ts - _last_signal_time.get(key,0) > COOLDOWN_1M:

                _last_signal_time[key] = now_ts

                msg = (
                    f"⏱ SHORT 1M\n\n"
                    f"{nome}\n"
                    f"Preço cruzou MA200\n"
                    f"Preço: {last_close:.6f}\n"
                    f"Volume 1.5x+\n"
                    f"{data_hora_atual}"
                )

                await send(msg)

async def analisar_1d(sym, klines):

    closes = [float(k[4]) for k in klines]
    volumes = [float(k[5]) for k in klines]

    ma50 = moving_average(closes[:-1],50)

    last_close = closes[-1]
    prev_close = closes[-2]

    vol_atual = volumes[-1]
    vol_prev = volumes[-2]

    vol_ok = vol_atual >= vol_prev * 1.5

    nome = sym.replace("USDT","")
    data_hora_atual = now()

    key = f"{sym}_1D_MA50"

    now_ts = time.time()

    if vol_ok:

        if prev_close < ma50 and last_close > ma50:

            if now_ts - _last_signal_time.get(key,0) > COOLDOWN_1D:

                _last_signal_time[key] = now_ts

                msg = (
                    f"📅 LONG 1D\n\n"
                    f"{nome}\n"
                    f"Preço cruzou MA50\n"
                    f"Preço: {last_close:.6f}\n"
                    f"Volume 1.5x+\n"
                    f"{data_hora_atual}"
                )

                await send(msg)

        elif prev_close > ma50 and last_close < ma50:

            if now_ts - _last_signal_time.get(key,0) > COOLDOWN_1D:

                _last_signal_time[key] = now_ts

                msg = (
                    f"📅 SHORT 1D\n\n"
                    f"{nome}\n"
                    f"Preço cruzou MA50\n"
                    f"Preço: {last_close:.6f}\n"
                    f"Volume 1.5x+\n"
                    f"{data_hora_atual}"
                )

                await send(msg)

async def monitor_loop():

    await send(f"SENTINELA ATIVO EM: {now()}")

    while True:

        try:

            async with aiohttp.ClientSession() as s:

                data24 = await get_json(s, f"{BINANCE}/api/v3/ticker/24hr")

                if not data24:
                    await asyncio.sleep(5)
                    continue

                pool = [x["symbol"] for x in data24
                        if x.get("symbol","").endswith("USDT")
                        and par_eh_valido(x["symbol"])]

                for x in data24:

                    sym = x["symbol"]

                    if not sym.endswith("USDT"):
                        continue

                    if not par_eh_valido(sym):
                        continue

                    vol24 = float(x.get("quoteVolume",0))

                    if 5_000_000 <= vol24 <= 100_000_000:

                        kl_1m = await get_json(
                            s,
                            f"{BINANCE}/api/v3/klines",
                            {"symbol":sym,"interval":"1m","limit":210}
                        )

                        if kl_1m:
                            await analisar_1m(sym, kl_1m)

                    kl_1d = await get_json(
                        s,
                        f"{BINANCE}/api/v3/klines",
                        {"symbol":sym,"interval":"1d","limit":100}
                    )

                    if kl_1d:
                        await analisar_1d(sym, kl_1d)

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
