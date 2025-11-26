import os, asyncio, aiohttp, time, threading
from datetime import datetime
from flask import Flask

# =========================
# CONFIG
# =========================
BINANCE = "https://api.binance.com"
TOP_N = int(os.getenv("TOP_N", "50"))
SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL", "180"))
MIN_QV_USDT = float(os.getenv("MIN_QV_USDT", "500000"))

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()
PAIRS = os.getenv("PAIRS", "").strip()

# =========================
# FILTRO — SPOT REAL APENAS
# =========================
def par_eh_valido(sym):
    base = sym.replace("USDT", "").upper()
    if sym.endswith(("UPUSDT","DOWNUSDT","BULLUSDT","BEARUSDT")): return False
    if base.startswith(("W","M","X")): return False
    lixo_contains = ("INU","PEPE","FLOKI","BABY","CAT","DOGE2","SHIB2","MOON","MEME","AI","OLD","NEW","GEN","PUP","PUPPY","TURBO","WIF","2","3")
    if any(k in base for k in lixo_contains): return False
    bloquear = ("EUR","BRL","TRY","GBP","AUD","CAD","CHF","RUB","MXN","ZAR","BKRW","BVND","IDRT","FDUSD","BUSD","TUSD","USDC","USDP","USDE","PAXG")
    if base in bloquear: return False
    return True

# =========================
# FLASK
# =========================
app = Flask(__name__)
@app.route("/")
def home(): return "OURO-SENTINELA ATIVO", 200
@app.route("/health")
def health(): return "OK", 200

# =========================
# BASE
# =========================
def now(): return datetime.now().strftime("%H:%M:%S")

async def send(msg):
    if not TELEGRAM_TOKEN or not CHAT_ID: return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        async with aiohttp.ClientSession() as s:
            await s.post(url, json={"chat_id": CHAT_ID, "text": msg})
    except: pass

async def get_json(session, url, params=None):
    try:
        async with session.get(url, params=params, timeout=15) as r:
            return await r.json()
    except: return None

async def fetch_24hr(session): return await get_json(session, f"{BINANCE}/api/v3/ticker/24hr")

async def fetch_klines(session, sym, interval="5m", limit=200):
    return await get_json(session, f"{BINANCE}/api/v3/klines", {"symbol": sym, "interval": interval, "limit": limit})

# =========================
# INDICADORES
# =========================
def ema(values, period):
    if not values: return 0.0
    k = 2/(period+1); e = values[0]
    for v in values[1:]: e = v*k + e*(1-k)
    return e

def macd(values):
    if len(values) < 35: return 0.0, 0.0
    fast = ema(values[-26:],12); slow = ema(values[-35:],26)
    line = fast - slow; signal = ema([line]*9,9); hist = line - signal
    return line, hist

def rsi(values, period=14):
    if len(values) < period+1: return 50.0
    gains=[max(values[i]-values[i-1],0.0) for i in range(1,len(values))]
    losses=[max(values[i-1]-values[i],0.0) for i in range(1,len(values))]
    ag=sum(gains[-period:])/period; al=(sum(losses[-period:])/period) or 1e-9
    return 100.0 - (100.0/(1.0+ag/al))

def sma(values, n):
    if len(values)<n: return sum(values)/max(len(values),1)
    return sum(values[-n:])/n

# =========================
# ALERTAS
# =========================
_last_alert = {}

def _volume_strength(vols):
    if len(vols) < 9: return 1.0
    return vols[-1] / (sma(vols, 9) + 1e-9)

async def alerta_fundo(session, sym, o, h, l, c, v):
    n = len(c)
    if n < 60: return

    # M200 abaixo (mantém filtro de segurança)
    ema200 = ema(c[-200:],200) if n>=200 else c[-1]+999
    if c[-1] > ema200: return

    # 1) QUEDA RECENTE (sem número fixo): média das últimas 8 variações negativa
    downs = [c[i]-c[i-1] for i in range(n-8, n) if i>0]
    if not downs or (sum(downs)/len(downs)) >= 0: return

    # 2) MICRO-BASE: últimas 3–6 velas com corpos pequenos e ranges comprimidos
    kmin, kmax = 3, 6
    base_k = min(kmax, max(kmin, 6))
    bodies = [abs(c[-i]-o[-i]) for i in range(2, base_k+2)]
    ranges = [h[-i]-l[-i] for i in range(2, base_k+2)]
    if not bodies or not ranges: return
    avg_body = sum(bodies)/len(bodies)
    avg_range = sum(ranges)/len(ranges)
    if avg_range > (sma(ranges, len(ranges)) * 1.2): return  # não comprimido

    # 3) PRIMEIRA VERDE FORTE/ANTECIPADA:
    #    - corpo >= 1.6x média dos corpos da base (mínimo 2x você já pediu antes; aqui 1.6 para antecipar)
    #    - fecha acima da máxima das últimas 2 velas OU cruza EMA9
    body_now = abs(c[-1]-o[-1])
    ema9_now = ema(c[-9:],9)
    cond_body = body_now >= max(2.0*avg_body, 1e-9)  # ">= 2x" (sem teto)
    cond_break = (c[-1] > max(h[-2], h[-3])) or (o[-1] < ema9_now <= c[-1])

    # 4) VOLUME FORTE
    vs = _volume_strength(v)
    cond_vol = vs >= 1.4  # >=140% da média 9

    # 5) RSI virando para cima
    rsi_now = rsi(c, 14)
    rsi_up = rsi_now >= 45

    if not (cond_body and cond_break and cond_vol and rsi_up):
        return

    # cooldown
    nowt = time.time()
    if nowt - _last_alert.get(sym, 0) < 900: return
    _last_alert[sym] = nowt

    nome = sym.replace("USDT","")
    msg = (
        "🔔 POSSÍVEL FUNDO DE POÇO\n\n"
        f"{nome}\n"
        f"\n"
        f"Preço: {c[-1]:.6f}\n"
        "Queda + base comprimida + 1ª verde forte (>=2×) com volume e rompendo topo/EMA9."
    )
    await send(msg)
    print(f"[{now()}] ALERTA ENVIADO: {sym}")

# =========================
# LOOP
# =========================
async def monitor_loop():
    await send("🟢 OURO-SENTINELA INICIADO")
    print("OURO-SENTINELA RODANDO...")

    while True:
        try:
            async with aiohttp.ClientSession() as s:
                data24 = await fetch_24hr(s)
                if not data24 or isinstance(data24, dict):
                    print(f"[{now()}] Erro ao puxar 24h"); await asyncio.sleep(5); continue

                allow = set(PAIRS.split(",")) if PAIRS else None
                pool = []
                for x in data24:
                    if not isinstance(x, dict): continue
                    sym = x.get("symbol"); vol = x.get("quoteVolume")
                    if not sym or not vol: continue
                    if not sym.endswith("USDT"): continue
                    if not par_eh_valido(sym): continue
                    if allow and sym not in allow: continue
                    try:
                        if float(vol) >= MIN_QV_USDT: pool.append((sym, float(vol)))
                    except: pass

                symbols = [s for s,_ in sorted(pool, key=lambda t: t[1], reverse=True)]
                print(f"[{now()}] Monitorando {len(symbols)} pares...")

                for sym in symbols:
                    kl = await fetch_klines(s, sym, "5m", 200)
                    if not kl: continue

                    o = [float(k[1]) for k in kl]
                    h = [float(k[2]) for k in kl]
                    l = [float(k[3]) for k in kl]
                    c = [float(k[4]) for k in kl]
                    v = [float(k[5]) for k in kl]

                    await alerta_fundo(s, sym, o, h, l, c, v)

                await asyncio.sleep(SCAN_INTERVAL)

        except Exception as e:
            print(f"[{now()}] LOOP ERRO: {e}")
            await asyncio.sleep(5)

# =========================
# THREAD
# =========================
def start_bot(): asyncio.run(monitor_loop())
t = threading.Thread(target=start_bot, daemon=True); t.start()

# =========================
# FLASK
# =========================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
