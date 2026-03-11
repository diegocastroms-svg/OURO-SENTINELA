import os, asyncio, aiohttp, time, threading
from datetime import datetime, timedelta
from flask import Flask

BINANCE = "https://api.binance.com"

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()

SCAN_INTERVAL = 30
MIN_QV_USDT = 30_000_000
COOLDOWN = 3600

TF_1M = "1m"

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

async def analisar_tendencia(sym, klines):

    closes = [float(k[4]) for k in klines]
    volumes = [float(k[5]) for k in klines]

    ma9 = moving_average(closes, 9)
    ma20 = moving_average(closes, 20)
    ma200 = moving_average(closes, 200)

    vol_atual = volumes[-1]
    vol_prev1 = volumes[-2]
    vol_prev2 = volumes[-3]

    last_close = closes[-1]
    prev_close = closes[-2]

    nome = sym.replace("USDT","")
    data_hora_atual = now()

    vol_ok = vol_atual > (max(vol_prev1, vol_prev2) * 1.5)

    key1 = f"{sym}_MA200"
    key2 = f"{sym}_MA9MA20"

    now_ts = time.time()

    # ALERTA CRUZAMENTO PREÇO X MA200

    if vol_ok:

        if prev_close < ma200 and last_close > ma200:

            if now_ts - _last_signal_time.get(key1,0) > COOLDOWN:

                _last_signal_time[key1] = now_ts

                msg = (
                    f"🚀 LONG MA200\n\n"
                    f"{nome}\n"
                    f"Cruzou MA200\n"
                    f"Preço: {last_close:.6f}\n"
                    f"Volume: 1.5x+\n"
                    f"{data_hora_atual}"
                )

                await send(msg)

        elif prev_close > ma200 and last_close < ma200:

            if now_ts - _last_signal_time.get(key1,0) > COOLDOWN:

                _last_signal_time[key1] = now_ts

                msg = (
                    f"🔻 SHORT MA200\n\n"
                    f"{nome}\n"
                    f"Cruzou MA200\n"
                    f"Preço: {last_close:.6f}\n"
                    f"Volume: 1.5x+\n"
                    f"{data_hora_atual}"
                )

                await send(msg)

    # ALERTA REVERSÃO MA9 x MA20

    if vol_ok:

        prev_ma9 = moving_average(closes[:-1], 9)
        prev_ma20 = moving_average(closes[:-1], 20)

        prev2_ma20 = moving_average(closes[:-2], 20)

        ma20_caindo = prev2_ma20 > prev_ma20
        ma20_subindo = prev2_ma20 < prev_ma20

        # REVERSÃO PARA LONG

        if prev_ma9 < prev_ma20 and ma9 > ma20 and ma20_caindo:

            if now_ts - _last_signal_time.get(key2,0) > COOLDOWN:

                _last_signal_time[key2] = now_ts

                msg = (
                    f"🟢 REVERSÃO LONG\n\n"
                    f"{nome}\n"
                    f"MA9 cruzou MA20\n"
                    f"Tendência anterior: QUEDA\n"
                    f"Preço: {last_close:.6f}\n"
                    f"Volume: 1.5x+\n"
                    f"{data_hora_atual}"
                )

                await send(msg)

        # REVERSÃO PARA SHORT

        elif prev_ma9 > prev_ma20 and ma9 < ma20 and ma20_subindo:

            if now_ts - _last_signal_time.get(key2,0) > COOLDOWN:

                _last_signal_time[key2] = now_ts

                msg = (
                    f"🔴 REVERSÃO SHORT\n\n"
                    f"{nome}\n"
                    f"MA9 cruzou MA20\n"
                    f"Tendência anterior: ALTA\n"
                    f"Preço: {last_close:.6f}\n"
                    f"Volume: 1.5x+\n"
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
                        and par_eh_valido(x["symbol"])
                        and float(x.get("quoteVolume",0)) >= MIN_QV_USDT]

                for sym in pool:

                    kl_1m = await get_json(
                        s,
                        f"{BINANCE}/api/v3/klines",
                        {"symbol":sym,"interval":"1m","limit":210}
                    )

                    if kl_1m:
                        await analisar_tendencia(sym, kl_1m)

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
