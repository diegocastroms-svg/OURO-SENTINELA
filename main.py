import os, asyncio, aiohttp, time, threading, sys
from datetime import datetime, timedelta
from flask import Flask

BINANCE = "https://fapi.binance.com"

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()

SCAN_INTERVAL = 30

# Controle de estado: "bull", "bear" ou None
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
    invalid = ("BRL","TRY","GBP","AUD","CAD","CHF","MXN","ZAR","RUB","EUR","USD","BUSD","TUSD","FDUSD","USDC")
    
    delistadas = (
        "ALPACA", "A2Z", "BNX", "SXP", "LRC", "RDNT", "NTRN", "IDEX", "FORTH", "OMG", "WAVES", 
        "MKR", "BAL", "BLZ", "FTM", "LINA", "STMX", "NKN", "SC", "BAKE", "RAY", "KLAY", "FTT",
        "AMB", "LEVER", "KEY", "COMBO", "MDT", "OXT", "HIFI", "GLMR", "STRAX", "LOOM", "BOND",
        "ORBS", "STPT", "TOKEN", "SNT", "BADGER", "MYRO", "OMNI", "VOXEL", "VIDT", "NULS",
        "CHESS", "BSW", "QUICK", "NEIROETH", "UXLINK", "KDA", "PONKE", "HIPPO", "SLERF",
        "BID", "FUN", "XCN", "EPT", "MEMEFI", "FIS", "MILK", "OBOL",
        "OL", "RLS", "PUFFER", "VFY", "RVV", "42", "COMMON", "BDXN", "TANSSI", "ZRC",
        "SKATE", "DMC"
    )
    if any(k in base for k in delistadas):
        return False
    
    lixo = ("INU","PEPE","FLOKI","BABY","CAT","DOGE2","SHIB2","MOON","PUP","PUPPY","OLD","NEW")
    if any(k in base for k in lixo):
        return False
    return True

# ====================== ALTERADO ======================
def pegar_top_25_gainers(tickers):
    if not tickers or not isinstance(tickers, list):
        return []
    
    lista_usdt = [
        t for t in tickers
        if isinstance(t, dict) and t.get('symbol','').endswith('USDT') and par_eh_valido(t.get('symbol',''))
    ]
    
    top_25 = sorted(
        lista_usdt,
        key=lambda x: float(x.get('priceChangePercent', 0)),
        reverse=True
    )[:25]
    
    return [moeda['symbol'] for moeda in top_25]

# ====================== NOVA LÓGICA ======================
async def analisar_5m(sym, klines):
    closes = [float(k[4]) for k in klines]
    if len(closes) < 30:
        return

    ema9 = ema(closes, 9)
    last_close = closes[-1]
    nome = sym.replace("USDT", "")

    # Bollinger 20 atual
    if len(closes) < 20:
        return
    bb_middle = sum(closes[-20:]) / 20
    bb_std = (sum((x - bb_middle) ** 2 for x in closes[-20:]) / 20) ** 0.5
    bb_upper = bb_middle + (2 * bb_std)
    bb_lower = bb_middle - (2 * bb_std)
    bandwidth = (bb_upper - bb_lower) / bb_middle if bb_middle != 0 else 0

    # Bollinger da vela anterior (para detectar se está abrindo)
    if len(closes) >= 21:
        prev_closes = closes[-21:-1]
        bb_middle_prev = sum(prev_closes) / 20
        bb_std_prev = (sum((x - bb_middle_prev) ** 2 for x in prev_closes) / 20) ** 0.5
        bb_upper_prev = bb_middle_prev + (2 * bb_std_prev)
        bb_lower_prev = bb_middle_prev - (2 * bb_std_prev)
        bandwidth_prev = (bb_upper_prev - bb_lower_prev) / bb_middle_prev if bb_middle_prev != 0 else 0
        boca_abrindo = bandwidth > bandwidth_prev * 1.001   # pequena tolerância para evitar ruído
    else:
        boca_abrindo = False

    acima_ema = last_close > ema9[-1]
    abaixo_ema = last_close < ema9[-1]

    condicao_bull = acima_ema and boca_abrindo
    condicao_bear = abaixo_ema and boca_abrindo

    # Controle para evitar alertas repetidos
    status_atual = alert_status.get(sym, None)

    if condicao_bull:
        if status_atual != "bull":
            alert_status[sym] = "bull"
            msg = f"🚀 BOCA DE JACARÉ BULL 5M\n\n{nome}\nPreço: {last_close:.6f}\nPreço acima da EMA 9 + Bollinger abrindo\n{now()}"
            await send(msg)
            print(f"✅ ALERTA BULL ENVIADO → {nome} | Preço: {last_close:.6f}")
            sys.stdout.flush()

    elif condicao_bear:
        if status_atual != "bear":
            alert_status[sym] = "bear"
            msg = f"🐻 BOCA DE JACARÉ BEAR 5M\n\n{nome}\nPreço: {last_close:.6f}\nPreço abaixo da EMA 9 + Bollinger abrindo\n{now()}"
            await send(msg)
            print(f"✅ ALERTA BEAR ENVIADO → {nome} | Preço: {last_close:.6f}")
            sys.stdout.flush()

    else:
        # Perdeu o padrão → libera para novo alerta no futuro
        if status_atual is not None:
            alert_status[sym] = None
            print(f"   📉 {nome} perdeu o padrão (pronto para novo alerta)")
            sys.stdout.flush()

# ====================== MONITOR LOOP ======================
async def monitor_loop():
    print("🚀 Monitoramento FUTUROS iniciado")
    sys.stdout.flush()
    await send(f"SENTINELA 5M ATIVO EM: {now()}")
    
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

                top_25 = pegar_top_25_gainers(data24)
                
                print(f"✅ Top 25 Futuros: {', '.join(top_25) if top_25 else 'VAZIO'}")
                for sym in top_25:
                    for t in data24:
                        if t.get('symbol') == sym:
                            change = t.get('priceChangePercent', 'N/A')
                            print(f"   → {sym}  (+{change}%)")
                            break
                sys.stdout.flush()

                for sym in top_25:
                    kl_5m = await get_json(s, f"{BINANCE}/fapi/v1/klines", 
                                           {"symbol": sym, "interval": "5m", "limit": 100})
                    if kl_5m:
                        await analisar_5m(sym, kl_5m)
                    await asyncio.sleep(0.1)

            print(f"[{now()}] Ciclo finalizado - aguardando {SCAN_INTERVAL}s...\n")
            sys.stdout.flush()
            await asyncio.sleep(SCAN_INTERVAL)
            
        except Exception as e:
            print(f"❌ ERRO: {e}")
            sys.stdout.flush()
            await asyncio.sleep(10)

def start_flask():
    port = int(os.getenv("PORT", 10000))
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

if __name__ == "__main__":
    print("Iniciando Sentinela 5M...")
    sys.stdout.flush()
    threading.Thread(target=start_flask, daemon=True).start()
    asyncio.run(monitor_loop())
