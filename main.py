import os, asyncio, aiohttp, time, threading
from datetime import datetime, timedelta
from flask import Flask

BINANCE = "https://api.binance.com"

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()

SCAN_INTERVAL = 30
MIN_QV_USDT = 5_000_000

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

def ema(values, period):
    k = 2 / (period + 1)
    ema_vals = []
    for i, v in enumerate(values):
        if i == 0:
            ema_vals.append(v)
        else:
            ema_vals.append(v * k + ema_vals[-1] * (1 - k))
    return ema_vals

def calcular_macd(closes):
    ema8 = ema(closes, 8)
    ema17 = ema(closes, 17)

    macd = [a - b for a, b in zip(ema8, ema17)]
    signal = ema(macd, 9)

    return macd, signal

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

async def analisar_5m(sym, klines):

    closes = [float(k[4]) for k in klines]

    ema9 = ema(closes,9)
    ema20 = ema(closes,20)
    ema50 = ema(closes,50)

    ema9_prev = ema9[-2]
    ema20_prev = ema20[-2]

    ema9_atual = ema9[-1]
    ema20_atual = ema20[-1]
    ema50_atual = ema50[-1]

    macd, signal = calcular_macd(closes)

    macd_atual = macd[-1]
    signal_atual = signal[-1]

    last_close = closes[-1]

    nome = sym.replace("USDT","")
    data_hora_atual = now()

    key = f"{sym}_5M_SETUP"
    now_ts = time.time()

    # LONG (simples e direto)
    macd_verde = macd_atual > signal_atual

    ema_alinhando = (
        ema9_atual > ema20_atual > ema50_atual and
        (ema9_atual - ema20_atual) > (ema9_prev - ema20_prev)
    )

    if macd_verde and ema_alinhando:

        if now_ts - _last_signal_time.get(key,0) > COOLDOWN_15M:

            _last_signal_time[key] = now_ts

            msg = (
                f"🚀 LONG\n\n"
                f"{nome}\n"
                f"MACD verde + EMAs alinhando\n"
                f"Preço: {last_close:.6f}\n"
                f"{data_hora_atual}"
            )

            await send(msg)

    # SHORT
    macd_vermelho = macd_atual < signal_atual

    ema_alinhando_short = (
        ema9_atual < ema20_atual < ema50_atual and
        (ema20_atual - ema9_atual) > (ema20_prev - ema9_prev)
    )

    elif macd_vermelho and ema_alinhando_short:

        if now_ts - _last_signal_time.get(key,0) > COOLDOWN_15M:

            _last_signal_time[key] = now_ts

            msg = (
                f"🔻 SHORT\n\n"
                f"{nome}\n"
                f"MACD vermelho + EMAs alinhando\n"
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

                    if vol24 >= MIN_QV_USDT:

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
