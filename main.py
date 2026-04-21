import os, asyncio, aiohttp, time, threading, sys, json
from datetime import datetime, timedelta
from flask import Flask

BINANCE = "https://fapi.binance.com"

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()

SCAN_INTERVAL = 10 # Reduzi para 10s para capturar o toque em tempo real mais rápido

alert_status = {}

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
    if not values: return []
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

async def pegar_top_24h(session):
    data24 = await get_json(session, f"{BINANCE}/fapi/v1/ticker/24hr")
    if not data24: return []
    lista = [
        t for t in data24
        if t.get('symbol','').endswith('USDT')
        and par_eh_valido(t.get('symbol',''))
        and float(t.get('quoteVolume', 0)) > 20000000
    ]
    ordenado = sorted(lista, key=lambda x: float(x.get('priceChangePercent', 0)), reverse=True)
    top_30 = ordenado[:30]
    return [t['symbol'] for t in top_30]

# ====================== NOVA LÓGICA DE SETUP ======================
async def analisar_5m(sym, klines):
    # klines[-1] é a vela atual (em formação)
    # klines[-2] é a vela anterior (fechada)
    
    fechados = [float(k[4]) for k in klines[:-1]] # Todos os fechados
    preco_atual = float(klines[-1][4])           # Preço do último tick
    preco_anterior = float(klines[-2][4])
    nome = sym.replace("USDT", "")

    if len(fechados) < 200: return

    # 1. CÁLCULO DA EMA 200
    # Calculamos a EMA considerando o preço atual para ver o cruzamento/proximidade real
    lista_com_atual = fechados + [preco_atual]
    ema200_lista = ema(lista_com_atual, 200)
    ema200_atual = ema200_lista[-1]

    # 2. CÁLCULO DAS BANDAS DE BOLLINGER (20, 2)
    def calc_bb(data):
        sma = sum(data[-20:]) / 20
        std = (sum((x - sma) ** 2 for x in data[-20:]) / 20) ** 0.5
        return sma + (2 * std), sma - (2 * std)

    bb_up_atual, bb_low_atual = calc_bb(lista_com_atual)
    bb_up_ant, bb_low_ant = calc_bb(fechados)

    # 3. FILTROS DE DISTÂNCIA E CRUZAMENTO
    distancia = abs(preco_atual - ema200_atual) / ema200_atual
    dentro_limite = distancia <= 0.015
    cruzou_ema = (preco_anterior < ema200_atual <= preco_atual) or (preco_anterior > ema200_atual >= preco_atual)

    if not (dentro_limite or cruzou_ema):
        return

    # 4. GATILHOS DE ENTRADA (Mudar para Short/Long sem esperar fechar)
    
    # LONG: Preço >= Banda Superior E Banda Superior virando para cima
    condicao_long = preco_atual >= bb_up_atual and bb_up_atual > bb_up_ant
    
    # SHORT: Preço <= Banda Inferior E Banda Inferior virando para baixo
    condicao_short = preco_atual <= bb_low_atual and bb_low_atual < bb_low_ant

    status_atual = alert_status.get(sym, None)

    if condicao_long and status_atual != "long":
        alert_status[sym] = "long"
        msg = f"🟢 SENTINELA LONG 5M (Ignição)\n{nome}\nPreço: {preco_atual:.6f}\nDist. EMA: {distancia:.2%}\n{now()}"
        await send(msg)

    elif condicao_short and status_atual != "short":
        alert_status[sym] = "short"
        msg = f"🔴 SENTINELA SHORT 5M (Ignição)\n{nome}\nPreço: {preco_atual:.6f}\nDist. EMA: {distancia:.2%}\n{now()}"
        await send(msg)
    
    # Reset de status se sair da banda para permitir novo alerta futuro
    elif not condicao_long and not condicao_short:
        alert_status[sym] = None

# ====================== MONITOR ======================
async def monitor_loop():
    while True:
        async with aiohttp.ClientSession() as s:
            top_lista = await pegar_top_24h(s)
            for sym in top_lista:
                # Limit 201 para garantir cálculo da EMA 200
                kl_5m = await get_json(s, f"{BINANCE}/fapi/v1/klines",
                                       {"symbol": sym, "interval": "5m", "limit": 210})
                if kl_5m:
                    await analisar_5m(sym, kl_5m)
                await asyncio.sleep(0.2)
        await asyncio.sleep(SCAN_INTERVAL)

async def main():
    await monitor_loop()

def start_flask():
    port = int(os.getenv("PORT", 10000))
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

if __name__ == "__main__":
    threading.Thread(target=start_flask, daemon=True).start()
    asyncio.run(main())
