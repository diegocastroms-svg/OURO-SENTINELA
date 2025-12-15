
import os, asyncio, aiohttp, time, threading
from datetime import datetime
from flask import Flask

BINANCE = "https://api.binance.com"

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()

SCAN_INTERVAL = 30
MIN_QV_USDT = 2_000_000

# timeframes
TF_1H = "1h"
TF_4H = "4h"
TF_1D = "1d"

# cooldowns
COOLDOWN_1H = 1800       # 30 min
COOLDOWN_4H = 7200      # 2 horas
COOLDOWN_1D = 43200     # 12 horas

app = Flask(__name__)
@app.route("/")
def home():
    return "SENTINELA MULTI-TIMEFRAME ATIVO", 200

@app.route("/health")
def health():
    return "OK", 200

def now():
    return datetime.now().strftime("%H:%M:%S")

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

async def fetch_24hr(session):
    return await get_json(session, f"{BINANCE}/api/v3/ticker/24hr")

async def fetch_klines(session, sym, interval, limit=50):
    return await get_json(
        session,
        f"{BINANCE}/api/v3/klines",
        {"symbol": sym, "interval": interval, "limit": limit}
    )

# =========================
# FILTRO DE MOEDAS
# =========================
def par_eh_valido(sym):
    base = sym.replace("USDT", "").upper()

    invalid = (
        "BRL","TRY","GBP","AUD","CAD","CHF","MXN","ZAR","RUB",
        "BKRW","BVND","IDRT",
        "BUSD","TUSD","FDUSD","USDC","USDP","USDE","USDD",
        "USDX","USDJ","PAXG","BFUSD"
    )

    if base in invalid:
        return False

    lixo = (
        "INU","PEPE","FLOKI","BABY","CAT","DOGE2","SHIB2","MOON",
        "MEME","OLD","NEW","PUP","PUPPY","TURBO","WIF","AI"
    )
    if any(k in base for k in lixo):
        return False

    if sym.endswith(("UPUSDT","DOWNUSDT","BULLUSDT","BEARUSDT")):
        return False

    return True

# =========================
# RSI
# =========================
def rsi(values, period=14):
    if len(values) < period + 1:
        return 50
    gains = [max(values[i] - values[i-1], 0) for i in range(1, len(values))]
    losses = [max(values[i-1] - values[i], 0) for i in range(1, len(values))]
    ag = sum(gains[-period:]) / period
    al = sum(losses[-period:]) / period or 1e-9
    return 100 - (100 / (1 + ag / al))

# =========================
# ALERTAS
# =========================

_last_alert_1h = {}
_last_alert_4h = {}
_last_alert_1d = {}

# ---- ALERTA 1H (RSI < 35)
async def alerta_rsi_1h(session, sym, closes, highs, lows):
    r = rsi(closes)
    if r >= 35:
        return

    mb = sum(closes[-20:]) / 20
    sd = (sum((c - mb)**2 for c in closes[-20:]) / 20)**0.5
    dn = mb - 2*sd
    if closes[-1] > dn:
        return

    nowt = time.time()
    if nowt - _last_alert_1h.get(sym, 0) < COOLDOWN_1H:
        return
    _last_alert_1h[sym] = nowt

    nome = sym.replace("USDT", "")
    msg = (
        f"🔵 RSI 1H < 35\n\n"
        f"{nome}\n\n"
        f"Preço: {closes[-1]:.6f}\n"
        f"RSI: {r:.2f}"
    )
    await send(msg)
    print(f"[{now()}] ALERTA 1H: {sym}")

# ---- ALERTA 4H (RSI < 38)
async def alerta_rsi_4h(session, sym, closes, highs, lows):
    r = rsi(closes)
    if r >= 38:
        return

    mb = sum(closes[-20:]) / 20
    sd = (sum((c - mb)**2 for c in closes[-20:]) / 20)**0.5
    dn = mb - 2*sd
    if closes[-1] > dn:
        return

    nowt = time.time()
    if nowt - _last_alert_4h.get(sym, 0) < COOLDOWN_4H:
        return
    _last_alert_4h[sym] = nowt

    nome = sym.replace("USDT", "")
    msg = (
        f"🟠 RSI 4H < 38\n\n"
        f"{nome}\n\n"
        f"Preço: {closes[-1]:.6f}\n"
        f"RSI: {r:.2f}"
    )
    await send(msg)
    print(f"[{now()}] ALERTA 4H: {sym}")

# ---- ALERTA 1D (RSI < 38)
async def alerta_rsi_1d(session, sym, closes, highs, lows):
    r = rsi(closes)
    if r >= 38:
        return

    mb = sum(closes[-20:]) / 20
    sd = (sum((c - mb)**2 for c in closes[-20:]) / 20)**0.5
    dn = mb - 2*sd
    if closes[-1] > dn:
        return

    nowt = time.time()
    if nowt - _last_alert_1d.get(sym, 0) < COOLDOWN_1D:
        return
    _last_alert_1d[sym] = nowt

    nome = sym.replace("USDT", "")
    msg = (
        f"🟣 RSI 1D < 38\n\n"
        f"{nome}\n\n"
        f"Preço: {closes[-1]:.6f}\n"
        f"RSI (1D): {r:.2f}\n"
        "Banda inferior + RSI diário em sobrevenda."
    )
    await send(msg)
    print(f"[{now()}] ALERTA 1D: {sym}")

# =========================
# LOOP PRINCIPAL
# =========================

async def monitor_loop():
    await send("🟢 SENTINELA MULTI-TIMEFRAME INICIADO")
    print("SENTINELA RODANDO...")

    while True:
        try:
            async with aiohttp.ClientSession() as s:

                data24 = await fetch_24hr(s)
                if not data24:
                    await asyncio.sleep(5)
                    continue

                pool = []
                for x in data24:
                    sym = x.get("symbol")
                    vol = x.get("quoteVolume")
                    if not sym or not vol:
                        continue
                    if not sym.endswith("USDT"):
                        continue
                    if not par_eh_valido(sym):
                        continue
                    if float(vol) >= MIN_QV_USDT:
                        pool.append(sym)

                print(f"[{now()}] Monitorando {len(pool)} pares...")

                for sym in pool:

                    # ==== 1H ====
                    kl_1h = await fetch_klines(s, sym, TF_1H)
                    if kl_1h:
                        closes = [float(k[4]) for k in kl_1h]
                        highs  = [float(k[2]) for k in kl_1h]
                        lows   = [float(k[3]) for k in kl_1h]
                        await alerta_rsi_1h(s, sym, closes, highs, lows)

                    # ==== 4H ====
                    kl_4h = await fetch_klines(s, sym, TF_4H)
                    if kl_4h:
                        closes = [float(k[4]) for k in kl_4h]
                        highs  = [float(k[2]) for k in kl_4h]
                        lows   = [float(k[3]) for k in kl_4h]
                        await alerta_rsi_4h(s, sym, closes, highs, lows)

                    # ==== 1D ====
                    kl_1d = await fetch_klines(s, sym, TF_1D)
                    if kl_1d:
                        closes = [float(k[4]) for k in kl_1d]
                        highs  = [float(k[2]) for k in kl_1d]
                        lows   = [float(k[3]) for k in kl_1d]
                        await alerta_rsi_1d(s, sym, closes, highs, lows)

                await asyncio.sleep(SCAN_INTERVAL)

        except Exception as e:
            print(f"[{now()}] ERRO LOOP: {e}")
            await asyncio.sleep(5)

def start_bot():
    asyncio.run(monitor_loop())

threading.Thread(target=start_bot, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
