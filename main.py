import os, asyncio, aiohttp, time, threading, sys
from datetime import datetime, timedelta
from flask import Flask

BINANCE = "https://fapi.binance.com"

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()

SCAN_INTERVAL = 90

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
                print(f"❌ HTTP {r.status} em {url}")
                sys.stdout.flush()
                return None
            return await r.json()
    except Exception as e:
        print(f"❌ Erro ao buscar {url}: {e}")
        sys.stdout.flush()
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

    blocked = ("CREAM","PNT","MDX","ALPACA","A2Z","BNX","SXP","LRC","RDNT","NTRN","IDEX","FORTH","OMG","WAVES","MKR","BAL","BLZ","FTM","LINA","STMX","NKN","SC","BAKE","RAY","KLAY","FTT","AMB","LEVER","KEY","COMBO","MDT","OXT","HIFI","GLMR","STRAX","LOOM","BOND","ORBS","STPT","TOKEN","SNT","BADGER","MYRO","OMNI","VOXEL","VIDT","NULS","CHESS","BSW","QUICK","NEIROETH","UXLINK","KDA","PONKE","HIPPO","SLERF","BID","FUN","XCN","EPT","MEMEFI","FIS","MILK","OBOL","OL","RLS","PUFFER","VFY","RVV","42","COMMON","BDXN","TANSSI","ZRC","SKATE","DMC")

    if any(k in base for k in blocked):
        return False

    lixo = ("INU","PEPE","FLOKI","BABY","CAT","DOGE2","SHIB2","MOON","PUP","PUPPY","OLD","NEW")
    if any(k in base for k in lixo):
        return False
    return True

# 🔥 NOVA FUNÇÃO (15M REAL)
async def pegar_top_10_gainers_15m(session, data24):
    lista_usdt = [
        t for t in data24
        if t.get('symbol','').endswith('USDT')
        and par_eh_valido(t.get('symbol',''))
        and float(t.get('quoteVolume', 0)) > 20000000
    ]

    top_30 = sorted(
        lista_usdt,
        key=lambda x: float(x.get('priceChangePercent', 0)),
        reverse=True
    )[:30]

    variacoes = []

    for moeda in top_30:
        sym = moeda['symbol']

        klines = await get_json(session, f"{BINANCE}/fapi/v1/klines",
                                {"symbol": sym, "interval": "15m", "limit": 2})

        if not klines or len(klines) < 2:
            continue

        open_price = float(klines[-2][1])
        close_price = float(klines[-1][4])

        if open_price == 0:
            continue

        variacao = ((close_price - open_price) / open_price) * 100
        variacoes.append((sym, variacao))

    top_10 = sorted(variacoes, key=lambda x: x[1], reverse=True)[:10]

    return [s[0] for s in top_10]

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

    if condicao_bull:
        if status_atual != "bull":
            alert_status[sym] = "bull"
            msg = f"🟢 SENTINELA LONG 5M\n\n{nome}\nPreço: {last_close:.6f}\n{now()}"
            await send(msg)
            print(f"✅ ALERTA LONG ENVIADO → {nome}")
            sys.stdout.flush()

    elif condicao_bear:
        if status_atual != "bear":
            alert_status[sym] = "bear"
            msg = f"🔴 SENTINELA SHORT 5M\n\n{nome}\nPreço: {last_close:.6f}\n{now()}"
            await send(msg)
            print(f"✅ ALERTA SHORT ENVIADO → {nome}")
            sys.stdout.flush()

    else:
        if status_atual is not None:
            alert_status[sym] = None
            print(f"   📉 {nome} perdeu o padrão")

# ====================== MONITOR ======================
async def monitor_loop():
    print("🚀 Monitoramento FUTUROS iniciado")
    sys.stdout.flush()
    await send(f"SENTINELA 5M FUTUROS ATIVO EM: {now()}")

    while True:
        print(f"[{now()}] Iniciando ciclo...")
        sys.stdout.flush()
        try:
            async with aiohttp.ClientSession() as s:
                data24 = await get_json(s, f"{BINANCE}/fapi/v1/ticker/24hr")

                if not data24:
                    print("❌ Falha ao pegar tickers")
                    sys.stdout.flush()
                    await asyncio.sleep(10)
                    continue

                # 🔥 ALTERADO AQUI
                top_10 = await pegar_top_10_gainers_15m(s, data24)

                print(f"✅ Top 10 REAL 15M: {', '.join(top_10) if top_10 else 'VAZIO'}")
                sys.stdout.flush()

                for sym in top_10:
                    kl_5m = await get_json(s, f"{BINANCE}/fapi/v1/klines",
                                           {"symbol": sym, "interval": "5m", "limit": 100})
                    if kl_5m:
                        await analisar_5m(sym, kl_5m)
                    await asyncio.sleep(0.25)

            print(f"[{now()}] Ciclo finalizado - aguardando {SCAN_INTERVAL}s...\n")
            sys.stdout.flush()
            await asyncio.sleep(SCAN_INTERVAL)

        except Exception as e:
            print(f"❌ ERRO: {e}")
            sys.stdout.flush()
            await asyncio.sleep(15)

def start_flask():
    port = int(os.getenv("PORT", 10000))
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

if __name__ == "__main__":
    print("Iniciando Sentinela 5M Futuros...")
    sys.stdout.flush()
    threading.Thread(target=start_flask, daemon=True).start()
    asyncio.run(monitor_loop())
