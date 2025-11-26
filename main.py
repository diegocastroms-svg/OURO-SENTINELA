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
# FLASK
# =========================
app = Flask(__name__)

@app.route("/")
def home():
    return "OURO-SENTINELA ATIVO", 200

@app.route("/health")
def health():
    return "OK", 200


# =========================
# FUNÇÕES BÁSICAS
# =========================
def now():
    return datetime.now().strftime("%H:%M:%S")

async def send(msg):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        async with aiohttp.ClientSession() as s:
            await s.post(url, json={"chat_id": CHAT_ID, "text": msg})
    except:
        pass

async def get_json(session, url, params=None):
    try:
        async with session.get(url, params=params, timeout=10) as r:
            return await r.json()
    except:
        return None


# =========================
# BUSCAR LISTA REAL DE PARES SPOT
# =========================
async def fetch_spot_pairs():
    async with aiohttp.ClientSession() as s:
        data = await get_json(s, f"{BINANCE}/api/v3/exchangeInfo")
        spot = set()

        if not data or "symbols" not in data:
            return spot

        for x in data["symbols"]:
            if (
                x.get("status") == "TRADING"
                and x.get("isSpotTradingAllowed") == True
            ):
                spot.add(x["symbol"])

        return spot


# =========================
# 24h (NOME CONSERTADO)
# =========================
async def fetch24(session):
    return await get_json(session, f"{BINANCE}/api/v3/ticker/24hr")


# =========================
# KL Ines
# =========================
async def fetch_klines(session, sym, interval="5m", limit=200):
    return await get_json(
        session,
        f"{BINANCE}/api/v3/klines",
        {"symbol": sym, "interval": interval, "limit": limit}
    )


# =========================
# INDICADORES
# =========================
def ema(values, period):
    if not values:
        return 0.0
    k = 2 / (period + 1)
    e = values[0]
    for v in values[1:]:
        e = v * k + e * (1 - k)
    return e

def macd(values):
    if len(values) < 35:
        return 0.0, 0.0
    fast = ema(values[-26:], 12)
    slow = ema(values[-35:], 26)
    line = fast - slow
    signal = ema([line] * 9, 9)
    hist = line - signal
    return line, hist

def rsi(values, period=14):
    if len(values) < period + 1:
        return 50.0
    gains = [max(values[i] - values[i - 1], 0.0) for i in range(1, len(values))]
    losses = [max(values[i - 1] - values[i], 0.0) for i in range(1, len(values))]
    ag = sum(gains[-period:]) / period
    al = sum(losses[-period:]) / period or 1e-9
    return 100.0 - (100.0 / (1.0 + ag / al))


# =========================
# ALERTA DE FUNDO
# =========================
_last_alert = {}

async def alerta_fundo(session, sym, opens, closes):
    n = len(closes)
    if n < 60:
        return

    # M200
    if n >= 200:
        ema200 = ema(closes[-200:], 200)
    else:
        ema200 = closes[-1] + 999

    if closes[-1] > ema200:
        return

    # Lateralização
    cluster_len = 6
    cluster_opens = opens[-(cluster_len + 1):-1]
    cluster_closes = closes[-(cluster_len + 1):-1]
    cluster_bodies = [abs(c - o) for o, c in zip(cluster_opens, cluster_closes)]

    if not cluster_bodies:
        return

    avg_body = sum(cluster_bodies) / len(cluster_bodies)

    big_open = opens[-1]
    big_close = closes[-1]
    big_body = abs(big_close - big_open)

    if big_close <= big_open:
        return
    if big_body < avg_body * 2:
        return

    pre_region = closes[:-(cluster_len + 1)]
    if len(pre_region) < 5:
        return
    if max(pre_region[-20:]) <= max(cluster_closes):
        return

    nowt = time.time()
    if nowt - _last_alert.get(sym, 0) < 900:
        return
    _last_alert[sym] = nowt

    msg = (
        "🔔 POSSÍVEL FUNDO DE POÇO\n\n"
        f"{sym}\n"
        f"Preço: {closes[-1]:.6f}\n"
        "Queda antes + lateralização + vela de rompimento >= 2×."
    )
    await send(msg)
    print(f"[{now()}] ALERTA ENVIADO: {sym}")


# =========================
# LOOP PRINCIPAL
# =========================
async def monitor_loop():

    await send("🟢 OURO-SENTINELA INICIADO")
    print("OURO-SENTINELA RODANDO...")

    spot_pairs = await fetch_spot_pairs()
    print(f"[{now()}] PARES SPOT REAIS ENCONTRADOS: {len(spot_pairs)}")

    while True:
        try:
            async with aiohttp.ClientSession() as s:

                data24 = await fetch24(s)  # ← CORRIGIDO AQUI

                if not data24 or isinstance(data24, dict):
                    print(f"[{now()}] Erro ao puxar 24h")
                    await asyncio.sleep(5)
                    continue

                allow = set(PAIRS.split(",")) if PAIRS else None
                pool = []

                for x in data24:
                    if not isinstance(x, dict):
                        continue

                    sym = x.get("symbol")
                    vol = x.get("quoteVolume")

                    if not sym or not vol:
                        continue
                    if not sym.endswith("USDT"):
                        continue
                    if sym not in spot_pairs:
                        continue
                    if allow and sym not in allow:
                        continue

                    try:
                        if float(vol) >= MIN_QV_USDT:
                            pool.append((sym, float(vol)))
                    except:
                        pass

                symbols = [s for s, _ in sorted(pool, key=lambda t: t[1], reverse=True)]

                print("\n==============================")
                print(f"[{now()}] MONITORANDO {len(symbols)} PARES:")
                for s in symbols:
                    print("-", s)
                print("==============================\n")

                for sym in symbols:
                    kl = await fetch_klines(s, sym, "5m", 200)
                    if not kl:
                        continue

                    opens = [float(k[1]) for k in kl]
                    closes = [float(k[4]) for k in kl]

                    await alerta_fundo(s, sym, opens, closes)

                await asyncio.sleep(SCAN_INTERVAL)

        except Exception as e:
            print(f"[{now()}] LOOP ERRO: {e}")
            await asyncio.sleep(5)


# =========================
# THREAD
# =========================
def start_bot():
    asyncio.run(monitor_loop())

t = threading.Thread(target=start_bot, daemon=True)
t.start()


# =========================
# FLASK
# =========================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
