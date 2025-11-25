import os, asyncio, aiohttp, time
from datetime import datetime

# =========================
# CONFIG
# =========================
BINANCE = "https://api.binance.com"
TOP_N = int(os.getenv("TOP_N", "30"))
SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL", "180"))
MIN_QV_USDT = float(os.getenv("MIN_QV_USDT", "15000000"))

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()
PAIRS = os.getenv("PAIRS", "").strip()

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

async def fetch_24hr(session):
    return await get_json(session, f"{BINANCE}/api/v3/ticker/24hr")

async def fetch_klines(session, sym, interval="5m", limit=120):
    return await get_json(
        session,
        f"{BINANCE}/api/v3/klines",
        {"symbol": sym, "interval": interval, "limit": limit}
    )

# =========================
# INDICADORES
# =========================
def ema(values, period):
    k = 2 / (period + 1)
    e = values[0]
    for v in values[1:]:
        e = v * k + e * (1 - k)
    return e

def macd(values):
    if len(values) < 35:
        return 0, 0
    fast = ema(values[-26:], 12)
    slow = ema(values[-35:], 26)
    line = fast - slow
    signal = ema([line]*9, 9)
    hist = line - signal
    return line, hist

def rsi(values, period=14):
    if len(values) < period + 1:
        return 50
    gains = [max(values[i] - values[i-1], 0) for i in range(1, len(values))]
    losses = [max(values[i-1] - values[i], 0) for i in range(1, len(values))]
    ag = sum(gains[-period:]) / period
    al = sum(losses[-period:]) / period or 1e-9
    return 100 - (100 / (1 + ag/al))

# =========================
# ALERTA DE FUNDO
# =========================
_last_alert = {}

async def alerta_fundo(session, sym, closes):
    if len(closes) < 40:
        print(f"[{now()}] Ignorado {sym}: dados insuficientes")
        return

    ema200 = ema(closes[-200:], 200) if len(closes) >= 200 else closes[-1] + 999

    # só alerta se estiver abaixo da 200
    if closes[-1] > ema200:
        return

    ema9 = ema(closes[-20:], 9)
    ema20 = ema(closes[-20:], 20)
    rsi_now = rsi(closes)
    macd_line, hist = macd(closes)

    queda_forte = closes[-1] < closes[-6] * 0.985
    estabilizou = abs(closes[-1] - closes[-2]) <= closes[-1] * 0.003
    virada = ema9 > ema20 or (macd_line > 0 and hist > 0)

    if queda_forte and estabilizou and virada:
        nowt = time.time()
        last = _last_alert.get(sym, 0)
        if nowt - last < 900:
            return
        _last_alert[sym] = nowt

        msg = (
            f"🔔 POSSÍVEL FUNDO DETECTADO\n\n"
            f"{sym}\n"
            f"Preço: {closes[-1]:.6f}\n"
            f"RSI: {rsi_now:.1f}\n"
            f"MACD virando | EMA9 x EMA20\n"
            f"Abaixo da M200\n"
            f"Queda forte + estabilização + reversão."
        )
        await send(msg)
        print(f"[{now()}] ALERTA ENVIADO: {sym}")

# =========================
# LOOP PRINCIPAL
# =========================
async def monitor_loop():
    await send("🟢 OURO-SENTINELA INICIADO")

    print("====================================")
    print(" OURO-SENTINELA RODANDO NO RENDER...")
    print("====================================")

    while True:
        try:
            async with aiohttp.ClientSession() as s:

                data24 = await fetch_24hr(s)
                if not data24 or isinstance(data24, dict):
                    print(f"[{now()}] Erro ao puxar 24hr")
                    await asyncio.sleep(5)
                    continue

                allow = set(PAIRS.split(",")) if PAIRS else None
                pool = []

                for x in data24:
                    sym = x.get("symbol")
                    vol = x.get("quoteVolume")

                    if not sym or not vol:
                        continue
                    if not sym.endswith("USDT"):
                        continue
                    if allow and sym not in allow:
                        continue
                    try:
                        if float(vol) >= MIN_QV_USDT:
                            pool.append((sym, float(vol)))
                    except:
                        pass

                symbols = [s for s, _ in sorted(pool, key=lambda t: t[1], reverse=True)[:TOP_N]]

                print(f"[{now()}] Monitorando {len(symbols)} pares...")

                for sym in symbols:
                    kl = await fetch_klines(s, sym, "5m", 200)
                    if not kl:
                        continue

                    closes = [float(k[4]) for k in kl]
                    await alerta_fundo(s, sym, closes)

                await asyncio.sleep(SCAN_INTERVAL)

        except Exception as e:
            print(f"[{now()}] LOOP ERRO: {e}")
            await asyncio.sleep(5)

# =========================
# RODAR DIRETO
# =========================
if __name__ == "__main__":
    asyncio.run(monitor_loop())
