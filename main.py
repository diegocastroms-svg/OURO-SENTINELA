import os, asyncio, aiohttp, time, threading
from datetime import datetime, timedelta
from flask import Flask

BINANCE = "https://fapi.binance.com"

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()

SCAN_INTERVAL = 30

app = Flask(__name__)

@app.route("/")
def home():
    return "SENTINELA 5M - ATIVO", 200

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
    ema_vals = [values[0]]
    for v in values[1:]:
        ema_vals.append(v * k + ema_vals[-1] * (1 - k))
    return ema_vals

def par_eh_valido(sym):
    base = sym.replace("USDT", "").upper()
    invalid = ("BRL","TRY","GBP","AUD","CAD","CHF","MXN","ZAR","RUB","BKRW","BVND","IDRT",
               "BUSD","TUSD","FDUSD","USDC","USDP","USDE","USDD","USDX","USDJ","PAXG","BFUSD",
               "XUSD", "RLUSD", "EUR", "USD1")
    if base in invalid: return False
    lixo = ("INU","PEPE","FLOKI","BABY","CAT","DOGE2","SHIB2","MOON","MEME","OLD","NEW",
            "PUP","PUPPY","TURBO","WIF","AI")
    if any(k in base for k in lixo): return False
    if sym.endswith(("UPUSDT","DOWNUSDT","BULLUSDT","BEARUSDT")): return False
    return True

def pegar_top_5_gainers(tickers):
    lista_usdt = [
        t for t in tickers
        if t['symbol'].endswith('USDT') and par_eh_valido(t['symbol'])
    ]
    top_5 = sorted(
        lista_usdt,
        key=lambda x: float(x.get('priceChangePercent', 0)),
        reverse=True
    )[:5]
    return [moeda['symbol'] for moeda in top_5]

async def analisar_5m(sym, klines):
    closes = [float(k[4]) for k in klines]
    if len(closes) < 30:
        return

    ema9 = ema(closes, 9)
    last_close = closes[-1]

    if len(closes) >= 20:
        bb_media = sum(closes[-20:]) / 20
        bb_std = (sum((x - bb_media) ** 2 for x in closes[-20:]) / 20) ** 0.5
        bb_superior = bb_media + (2 * bb_std)
    else:
        return

    if len(closes) >= 21:
        bb_media_prev = sum(closes[-21:-1]) / 20
        bb_std_prev = (sum((x - bb_media_prev) ** 2 for x in closes[-21:-1]) / 20) ** 0.5
        bb_superior_prev = bb_media_prev + (2 * bb_std_prev)
    else:
        bb_superior_prev = bb_superior

    cond1 = closes[-1] > bb_media
    cond2 = (closes[-2] <= ema9[-2] * 1.01 or closes[-3] <= ema9[-3] * 1.01)
    cond3 = closes[-1] > closes[-2]
    cond4 = bb_superior > bb_superior_prev
    cond5 = closes[-1] >= bb_superior * 0.98

    if cond1 and cond2 and cond3 and cond4 and cond5:
        nome = sym.replace("USDT", "")
        data_hora = now()
        variacao_ate_bb = (last_close / bb_superior - 1) * 100

        msg = f"""🚀 SENTINELA 5M - SCALP RÁPIDO

{nome}
Preço: {last_close:.6f}
BB Superior: {bb_superior:.6f} ({variacao_ate_bb:+.1f}%)

✅ Rompimento + Pullback EMA9
✅ Banda expandindo
✅ Vela verde

⏱️ Alvo: 2\~3 velas
{data_hora}"""
        await send(msg)

async def monitor_loop():
    await send(f"SENTINELA 5M ATIVO EM: {now()}")
    print("🚀 Monitoramento iniciado - Futuros USDT")
    
    while True:
        try:
            async with aiohttp.ClientSession() as s:
                data24 = await get_json(s, f"{BINANCE}/fapi/v1/ticker/24hr")
                if not data24:
                    print("Erro ao pegar tickers")
                    await asyncio.sleep(5)
                    continue

                top_5 = pegar_top_5_gainers(data24)
                
                # ←←← MOSTRA AS MOEDAS NO LOG (importante)
                print(f"[{now()}] Top 5 Gainers Futuros: {', '.join(top_5)}")

                for sym in top_5:
                    kl_5m = await get_json(s, f"{BINANCE}/fapi/v1/klines",
                                           {"symbol": sym, "interval": "5m", "limit": 210})
                    if kl_5m:
                        await analisar_5m(sym, kl_5m)
                    await asyncio.sleep(0.08)

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
    port = int(os.getenv("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
