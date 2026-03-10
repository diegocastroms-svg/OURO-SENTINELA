import os, asyncio, aiohttp, time, threading
from datetime import datetime
from flask import Flask

BINANCE = "https://api.binance.com"

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()

SCAN_INTERVAL = 30
MIN_QV_USDT = 30_000_000

TF_15M = "15m"
TF_1H = "1h"

app = Flask(__name__)

@app.route("/")
def home():
    return "SENTINELA TREND-VOLUME ATIVO", 200

@app.route("/health")
def health():
    return "OK", 200

def now():
    return datetime.now().strftime("%d/%m/%Y %H:%M:%S")

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
    invalid = ("BRL","TRY","GBP","AUD","CAD","CHF","MXN","ZAR","RUB","BKRW","BVND","IDRT","BUSD","TUSD","FDUSD","USDC","USDP","USDE","USDD","USDX","USDJ","PAXG","BFUSD")
    if base in invalid: return False
    lixo = ("INU","PEPE","FLOKI","BABY","CAT","DOGE2","SHIB2","MOON","MEME","OLD","NEW","PUP","PUPPY","TURBO","WIF","AI")
    if any(k in base for k in lixo): return False
    if sym.endswith(("UPUSDT","DOWNUSDT","BULLUSDT","BEARUSDT")): return False
    return True

_last_processed = {}

async def analisar_tendencia(sym, klines, timeframe):
    key = f"{sym}_{timeframe}"
    candle_time = klines[-1][0]
    if _last_processed.get(key) == candle_time:
        return
    
    closes = [float(k[4]) for k in klines]
    volumes = [float(k[5]) for k in klines]
    
    ma9 = moving_average(closes, 9)
    ma21 = moving_average(closes, 21)
    vol_media = sum(volumes[-20:-1]) / 19
    vol_atual = volumes[-1]
    last_close = closes[-1]
    
    nome = sym.replace("USDT", "")
    data_hora_atual = now()

    if last_close > ma9 > ma21 and vol_atual > (vol_media * 1.5):
        _last_processed[key] = candle_time
        msg = (
            f"🚀 **{nome} LONG ({timeframe})**\n\n"
            f"📅 Data/Hora: {data_hora_atual}\n"
            f"✅ Tendência de Alta Confirmada\n"
            f"📊 Preço: {last_close:.6f}\n"
            f"🔥 Volume: {vol_atual/vol_media:.1f}x acima da média\n"
            f"📈 Alinhamento: MA9 > MA21"
        )
        await send(msg)
        print(f"[{data_hora_atual}] LONG {sym} {timeframe}")

    elif last_close < ma9 < ma21 and vol_atual > (vol_media * 1.5):
        _last_processed[key] = candle_time
        msg = (
            f"🔻 **{nome} SHORT ({timeframe})**\n\n"
            f"📅 Data/Hora: {data_hora_atual}\n"
            f"⚠️ Tendência de Baixa Confirmada\n"
            f"📊 Preço: {last_close:.6f}\n"
            f"🔥 Volume: {vol_atual/vol_media:.1f}x acima da média\n"
            f"📉 Alinhamento: MA9 < MA21"
        )
        await send(msg)
        print(f"[{data_hora_atual}] SHORT {sym} {timeframe}")

async def monitor_loop():
    await send(f"SENTINELA ATIVO EM: {now()}")
    while True:
        try:
            async with aiohttp.ClientSession() as s:
                data24 = await get_json(s, f"{BINANCE}/api/v3/ticker/24hr")
                if not data24:
                    await asyncio.sleep(5)
                    continue

                pool = [x["symbol"] for x in data24 if x.get("symbol", "").endswith("USDT") 
                        and par_eh_valido(x["symbol"]) 
                        and float(x.get("quoteVolume", 0)) >= MIN_QV_USDT]

                print(f"[{now()}] Analisando {len(pool)} pares...")

                for sym in pool:
                    kl_15 = await get_json(s, f"{BINANCE}/api/v3/klines", {"symbol": sym, "interval": "15m", "limit": 40})
                    if kl_15: await analisar_tendencia(sym, kl_15, "15m")

                    kl_1h = await get_json(s, f"{BINANCE}/api/v3/klines", {"symbol": sym, "interval": "1h", "limit": 40})
                    if kl_1h: await analisar_tendencia(sym, kl_1h, "1h")
                    
                    await asyncio.sleep(0.1)

            await asyncio.sleep(SCAN_INTERVAL)
        except Exception as e:
            print(f"[{now()}] Erro: {e}")
            await asyncio.sleep(10)

def start_bot():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(monitor_loop())

if __name__ == "__main__":
    threading.Thread(target=start_bot, daemon=True).start()
    port = int(os.getenv("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
