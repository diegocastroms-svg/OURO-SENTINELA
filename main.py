import os, asyncio, aiohttp, time, threading
from datetime import datetime
from flask import Flask

BINANCE = "https://api.binance.com"

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()

SCAN_INTERVAL = 30
MIN_QV_USDT = 2_000_000

COOLDOWN_4H = 7200      # 2 horas
COOLDOWN_1H = 1800      # 30 minutos

app = Flask(__name__)
@app.route("/")
def home():
    return "SENTINELA RSI 4H + 1H ATIVO", 200

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

# FILTRO DEFINITIVO
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

# RSI
def rsi(values, period=14):
    if len(values) < period + 1:
        return 50
    gains  = [max(values[i] - values[i-1], 0) for i in range(1, len(values))]
    losses = [max(values[i-1] - values[i], 0) for i in range(1, len(values))]
    ag = sum(gains[-period:]) / period
    al = sum(losses[-period:]) / period or 1e-9
    return 100 - (100 / (1 + ag / al))

_last_alert_4h = {}
_last_alert_1h = {}

# ---------------------------------------------------------------
# ALERTA 4H — RSI < 40 (NOVO)
# ---------------------------------------------------------------
async def alerta_rsi_4h(session, sym, closes, highs, lows):
    r = rsi(closes)
    if r >= 40:
        return

    mb = sum(closes[-20:]) / 20
    sd = (sum((c-mb)**2 for c in closes[-20:]) / 20) ** 0.5
    dn = mb - 2 * sd

    if closes[-1] > dn:
        return

    nowt = time.time()
    if nowt - _last_alert_4h.get(sym, 0) < COOLDOWN_4H:
        return
    _last_alert_4h[sym] = nowt

    nome = sym.replace("USDT", "")

    msg = (
        f"🟣 FUNDO 4H — RSI < 40 🟣\n\n"
        f"Ativo: {nome}\n\n"
        f"Preço: {closes[-1]:.6f}\n"
        f"RSI (4H): {r:.2f}\n\n"
        "Banda inferior + RSI < 40 (4H)"
    )

    await send(msg)
    print(f"[{now()}] ALERTA 4H: {sym}")

# ---------------------------------------------------------------
# ALERTA 1H — RSI < 40 (mantido)
# ---------------------------------------------------------------
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

    nome = sym.replace("USDT", "")

    msg = (
        f"🔷 FUNDO 1H — RSI < 40 🔷\n\n"
        f"Ativo: {nome}\n\n"
        f"Preço: {closes[-1]:.6f}\n"
        f"RSI (1H): {r:.2f}\n\n"
        "Banda inferior + RSI < 40 (1H)"
    )

    await send(msg)
    print(f"[{now()}] ALERTA 1H: {sym}")

# ---------------------------------------------------------------
# MONITOR LOOP
# ---------------------------------------------------------------
async def monitor_loop():
    await send("🟢 SENTINELA RSI 4H + 1H INICIADO")
    print("SENTINELA RSI 4H + 1H RODANDO...")

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

                    # 4H
                    kl_4h = await fetch_klines(s, sym, "4h")
                    if kl_4h:
                        closes = [float(k[4]) for k in kl_4h]
                        highs  = [float(k[2]) for k in kl_4h]
                        lows   = [float(k[3]) for k in kl_4h]
                        await alerta_rsi_4h(s, sym, closes, highs, lows)

                    # 1H
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
