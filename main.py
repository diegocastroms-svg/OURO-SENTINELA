import os, asyncio, aiohttp, time, threading, sys, json, websockets
from datetime import datetime, timedelta
from flask import Flask
from collections import deque

BINANCE = "https://fapi.binance.com"

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()

SCAN_INTERVAL = 30
WINDOW = 300  # 5 minutos

alert_status = {}
prices = {}

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
        async with session.get(url, params=params, timeout=15) as r:
            if r.status != 200:
                return None
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
    base = sym.replace("USDT", "").upper().strip()
    if len(base) < 2:
        return False

    lixo = ("INU","PEPE","FLOKI","BABY","CAT","DOGE2","SHIB2","MOON","PUP","PUPPY","OLD","NEW")
    if any(k in base for k in lixo):
        return False
    return True

# ====================== WEBSOCKET ======================
async def stream_prices():
    url = "wss://fstream.binance.com/ws/!ticker@arr"

    async with websockets.connect(url) as ws:
        while True:
            data = json.loads(await ws.recv())
            now_ts = time.time()

            for item in data:
                sym = item['s']
                if not sym.endswith("USDT"):
                    continue
                if not par_eh_valido(sym):
                    continue

                price = float(item['c'])

                if sym not in prices:
                    prices[sym] = deque()

                prices[sym].append((now_ts, price))

                # limpa histórico > 5 min
                while prices[sym] and now_ts - prices[sym][0][0] > WINDOW:
                    prices[sym].popleft()

# ====================== TOP 10 (5M REAL) ======================
def calcular_top10_5m():
    variacoes = []

    for sym, hist in prices.items():
        if len(hist) < 2:
            continue

        preco_antigo = hist[0][1]
        preco_atual = hist[-1][1]

        if preco_antigo == 0:
            continue

        variacao = ((preco_atual - preco_antigo) / preco_antigo) * 100
        variacoes.append((sym, variacao))

    top10 = sorted(variacoes, key=lambda x: x[1], reverse=True)[:10]

    return [s[0] for s in top10]

# ====================== LÓGICA ======================
async def analisar_5m(sym, klines):
    closes = [float(k[4]) for k in klines]
    if len(closes) < 30:
        return

    ema9 = ema(closes, 9)
    last_close = closes[-1]
    nome = sym.replace("USDT", "")

    if len(closes) < 20:
        return

    bb_middle = sum(closes[-20:]) / 20
    bb_std = (sum((x - bb_middle) ** 2 for x in closes[-20:]) / 20) ** 0.5
    bb_upper = bb_middle + (2 * bb_std)
    bb_lower = bb_middle - (2 * bb_std)

    if len(closes) >= 21:
        prev_closes = closes[-21:-1]
        bb_middle_prev = sum(prev_closes) / 20
        bb_std_prev = (sum((x - bb_middle_prev) ** 2 for x in prev_closes) / 20) ** 0.5
        bb_upper_prev = bb_middle_prev + (2 * bb_std_prev)
        bb_lower_prev = bb_middle_prev - (2 * bb_std_prev)

        bandwidth_prev = (bb_upper_prev - bb_lower_prev) / bb_middle_prev if bb_middle_prev != 0 else 0
        bandwidth = (bb_upper - bb_lower) / bb_middle if bb_middle != 0 else 0

        boca_abrindo = bandwidth > bandwidth_prev * 1.001
    else:
        boca_abrindo = False

    acima_bb = last_close > bb_middle
    abaixo_bb = last_close < bb_middle
    acima_ema = last_close > ema9[-1]
    abaixo_ema = last_close < ema9[-1]

    tocou_banda_superior = last_close >= bb_upper * 0.999
    tocou_banda_inferior = last_close <= bb_lower * 1.001

    condicao_bull = acima_bb and acima_ema and tocou_banda_superior and boca_abrindo
    condicao_bear = abaixo_bb and abaixo_ema and tocou_banda_inferior and boca_abrindo

    status_atual = alert_status.get(sym, None)

    if condicao_bull and status_atual != "bull":
        alert_status[sym] = "bull"
        msg = f"🟢 LONG 5M\n{nome}\n{last_close:.6f}\n{now()}"
        await send(msg)

    elif condicao_bear and status_atual != "bear":
        alert_status[sym] = "bear"
        msg = f"🔴 SHORT 5M\n{nome}\n{last_close:.6f}\n{now()}"
        await send(msg)

# ====================== MONITOR ======================
async def monitor_loop():
    while True:
        top10 = calcular_top10_5m()

        print(f"TOP 10 5M: {top10}")
        sys.stdout.flush()

        async with aiohttp.ClientSession() as s:
            for sym in top10:
                kl_5m = await get_json(s, f"{BINANCE}/fapi/v1/klines",
                                       {"symbol": sym, "interval": "5m", "limit": 100})

                if kl_5m:
                    await analisar_5m(sym, kl_5m)

                await asyncio.sleep(0.3)

        await asyncio.sleep(SCAN_INTERVAL)

# ====================== START ======================
async def main():
    await asyncio.gather(
        stream_prices(),
        monitor_loop()
    )

def start_flask():
    port = int(os.getenv("PORT", 10000))
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

if __name__ == "__main__":
    threading.Thread(target=start_flask, daemon=True).start()
    asyncio.run(main())
