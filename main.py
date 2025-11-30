import os, asyncio, aiohttp, time, threading
from datetime import datetime
from flask import Flask

BINANCE = "https://api.binance.com"

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()

SCAN_INTERVAL = 30
MIN_QV_USDT = 2_000_000

COOLDOWN_15M = 900
COOLDOWN_1H  = 1800

app = Flask(__name__)
@app.route("/")
def home():
    return "SENTINELA-RSI 15M + 1H ATIVO", 200

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

def par_eh_valido(sym):
    base = sym.replace("USDT", "").upper()

    if any(sym.endswith(s) for s in ["UPUSDT", "DOWNUSDT"]):
        return False

    bloqueadas = (
        "UP", "DOWN", "BUSD", "FDUSD", "USDC", "TUSD",
        "EUR", "USDE", "TRY", "GBP", "BRL", "AUD", "CAD",
        "BFUSD"
    )
    if base in bloqueadas:
        return False

    if base.startswith("USD") and base != "USDT":
        return False

    return True

def rsi(values, period=14):
    if len(values) < period + 1:
        return 50
    gains  = [max(values[i]-values[i-1],0) for i in range(1,len(values))]
    losses = [max(values[i-1]-values[i],0) for i in range(1,len(values))]
    ag = sum(gains[-period:])  / period
    al = sum(losses[-period:]) / period or 1e-9
    return 100 - (100 / (1 + ag / al))

_last_alert_15m = {}
_last_alert_1h  = {}

# --------------------------------------------
# ALERTA 15M — LAYOUT NOVO (AMARELO)
# --------------------------------------------
async def alerta_rsi_15m(session, sym, closes, highs, lows):
    r = rsi(closes)
    if r >= 35:
        return

    mb = sum(closes[-20:]) / 20
    sd = (sum((c-mb)**2 for c in closes[-20:]) / 20) ** 0.5
    dn = mb - 2 * sd

    if closes[-1] > dn:
        return

    nowt = time.time()
    if nowt - _last_alert_15m.get(sym, 0) < COOLDOWN_15M:
        return
    _last_alert_15m[sym] = nowt

    nome = sym.replace("USDT","")

    msg = (
        f"🔶 FUNDO 15M — RSI < 35 🔶\n\n"
        f"Moeda: {nome}\n\n"
        f"Preço: {closes[-1]:.6f}\n"
        f"RSI: {r:.2f}\n\n"
        f"Banda inferior tocada no 15m."
    )

    await send(msg)
    print(f"[{now()}] ALERTA 15M: {sym}")

# --------------------------------------------
# ALERTA 1H — LAYOUT NOVO (AZUL)
# --------------------------------------------
async def alerta_rsi_1h(session, sym, closes, highs, lows):
    r = rsi(closes)
    if r >= 40:
        return

    mb = sum(closes[-20:]) / 20
    sd = (sum((c-mb)**2 for c in closes[-20:]) / 20) ** 0.5
    dn = mb - 2 * sd

    if closes[-1] > dn:
        return

    nowt = time.time()
    if nowt - _last_alert_1h.get(sym, 0) < COOLDOWN_1H:
        return
    _last_alert_1h[sym] = nowt

    nome = sym.replace("USDT","")

    msg = (
        f"🔷 FUNDO 1H — RSI < 40 🔷\n\n"
        f"Ativo: {nome}\n\n"
        f"Preço atual: {closes[-1]:.6f}\n"
        f"RSI (1H): {r:.2f}\n\n"
        "Queda + banda inferior no 1H detectadas.\n"
        "Possível fundo mais forte."
    )

    await send(msg)
    print(f"[{now()}] ALERTA 1H: {sym}")

# --------------------------------------------
# MONITOR
# --------------------------------------------
async def monitor_loop():
    await send("🟢 SENTINELA RSI 15M + 1H INICIADO")
    print("SENTINELA-RSI 15M + 1H RODANDO...")

    while True:
        try:
            async with aiohttp.ClientSession() as s:

                data24 = await fetch_24hr(s)
                if not data24 or isinstance(data24, dict):
                    print("Erro ao puxar 24h")
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
                    try:
                        if float(vol) >= MIN_QV_USDT:
                            pool.append(sym)
                    except:
                        pass

                print(f"[{now()}] Monitorando {len(pool)} pares...")
                for p in pool:
                    print(f"- {p}")

                for sym in pool:

                    kl_15 = await fetch_klines(s, sym, "15m")
                    if kl_15:
                        closes = [float(k[4]) for k in kl_15]
                        highs  = [float(k[2]) for k in kl_15]
                        lows   = [float(k[3]) for k in kl_15]
                        await alerta_rsi_15m(s, sym, closes, highs, lows)

                    kl_1h = await fetch_klines(s, sym, "1h")
                    if kl_1h:
                        closes_h = [float(k[4]) for k in kl_1h]
                        highs_h  = [float(k[2]) for k in kl_1h]
                        lows_h   = [float(k[3]) for k in kl_1h]
                        await alerta_rsi_1h(s, sym, closes_h, highs_h, lows_h)

                await asyncio.sleep(SCAN_INTERVAL)

        except Exception as e:
            print(f"[{now()}] ERRO LOOP: {e}")
            await asyncio.sleep(5)

def start_bot():
    asyncio.run(monitor_loop())

threading.Thread(target=start_bot, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
