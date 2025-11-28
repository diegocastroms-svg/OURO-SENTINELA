import os, asyncio, aiohttp, time, threading
from datetime import datetime
from flask import Flask

BINANCE = "https://api.binance.com"

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()

SCAN_INTERVAL = 30
MIN_QV_USDT = 2_000_000
COOLDOWN = 900            # 15 minutos
TIMEFRAME = "15m"         # timeframe corrigido

app = Flask(__name__)
@app.route("/")
def home():
    return "SENTINELA-RSI35 15M ATIVO", 200

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

async def fetch_klines(session, sym, limit=50):
    return await get_json(
        session,
        f"{BINANCE}/api/v3/klines",
        {"symbol": sym, "interval": TIMEFRAME, "limit": limit}
    )

# =====================================================
# BLOCO CORRIGIDO — LISTA EXATA DAS BLOQUEADAS DA FOTO
# =====================================================
def par_eh_valido(sym):
    base = sym.replace("USDT", "").upper()

    # impedir UP/DOWN
    if any(sym.endswith(s) for s in ["UPUSDT", "DOWNUSDT"]):
        return False

    # BLOQUEADAS DA FOTO (EXATAMENTE COMO VOCÊ MANDOU)
    bloqueadas = (
        "UP", "DOWN", "BUSD", "FDUSD", "USDC", "TUSD",
        "EUR", "USDE", "TRY", "GBP", "BRL", "AUD", "CAD"
    )
    if base in bloqueadas:
        return False

    # bloquear USD1 / USD2 / USD3 / USDX
    if base.startswith("USD") and base != "USDT":
        return False

    return True

# =====================================================
# CÁLCULO DE RSI
# =====================================================
def rsi(values, period=14):
    if len(values) < period + 1:
        return 50

    gains = [max(values[i] - values[i - 1], 0) for i in range(1, len(values))]
    losses = [max(values[i - 1] - values[i], 0) for i in range(1, len(values))]

    ag = sum(gains[-period:]) / period
    al = sum(losses[-period:]) / period or 1e-9

    return 100 - (100 / (1 + ag / al))


_last_alert = {}

# =====================================================
# ALERTA RSI < 35 NA BANDA INFERIOR
# =====================================================
async def alerta_rsi(session, sym, closes, highs, lows):
    r = rsi(closes)

    if r >= 35:
        return

    mb = sum(closes[-20:]) / 20
    sd = (sum((c - mb) ** 2 for c in closes[-20:]) / 20) ** 0.5
    dn = mb - 2 * sd

    if closes[-1] > dn:
        return

    nowt = time.time()
    if nowt - _last_alert.get(sym, 0) < COOLDOWN:
        return
    _last_alert[sym] = nowt

    nome = sym.replace("USDT", "")

    msg = (
        f"🔔 RSI < 35\n\n"
        f"{nome}\n\n"
        f"Preço: {closes[-1]:.6f}\n"
        f"RSI: {r:.2f}\n"
        f"Banda inferior + RSI < 35"
    )

    await send(msg)
    print(f"[{now()}] ALERTA: {sym}")

# =====================================================
# MONITOR LOOP
# =====================================================
async def monitor_loop():
    await send("🟢 SENTINELA RSI<35 15M INICIADO")
    print("SENTINELA-RSI35 15M RODANDO...")

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
                    kl = await fetch_klines(s, sym)
                    if not kl:
                        continue

                    closes = [float(k[4]) for k in kl]
                    highs  = [float(k[2]) for k in kl]
                    lows   = [float(k[3]) for k in kl]

                    await alerta_rsi(s, sym, closes, highs, lows)

                await asyncio.sleep(SCAN_INTERVAL)

        except Exception as e:
            print(f"[{now()}] ERRO LOOP: {e}")
            await asyncio.sleep(5)

# =====================================================
# START BOT
# =====================================================
def start_bot():
    asyncio.run(monitor_loop())

threading.Thread(target=start_bot, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
