import os, asyncio, aiohttp, time, threading
from datetime import datetime
from flask import Flask

# =========================
# CONFIG
# =========================
BINANCE = "https://api.binance.com"
TOP_N = 200  # monitora praticamente tudo
SCAN_INTERVAL = 60
MIN_QV_USDT = 2_000_000  # volume mínimo 2 milhões

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()

MOEDAS_MORTAS = [
    "USDC", "USDT", "FDUSD", "TUSD", "BUSD", "EUR", "BRL",
    "TRY", "GBP", "AUD", "CAD", "RUB", "UAH", "VAI", "DAI",
]

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
# FUNÇÕES
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

async def fetch_klines(session, sym, interval="5m", limit=200):
    return await get_json(
        session,
        f"{BINANCE}/api/v3/klines",
        {"symbol": sym, "interval": interval, "limit": limit}
    )


# =========================
# INDICADORES
# =========================
def ma(values, period):
    if len(values) < period:
        return values[-1] + 999999
    return sum(values[-period:]) / period


# =========================
# ALERTA DE FUNDO — VELAS PEQUENAS + CANDLE FORTE 2×
# =========================
_last_alert = {}

async def alerta_fundo(session, sym, closes):

    if len(closes) < 210:
        return

    # MA200
    ma200 = ma(closes, 200)

    # abaixo da 200
    if closes[-1] > ma200:
        return

    # === 2) compressão ===
    corpos = [abs(closes[i] - closes[i-1]) for i in range(-6, -1)]
    if not all(c < closes[-1] * 0.004 for c in corpos):
        return

    media_corpos = sum(corpos) / len(corpos)

    # === 3) candle forte 2× ===
    corpo_atual = abs(closes[-1] - closes[-2])
    if corpo_atual < media_corpos * 2:
        return

    # === 4) rompendo pra cima ===
    if closes[-1] <= closes[-2]:
        return

    # === cooldown ===
    ts = time.time()
    if ts - _last_alert.get(sym, 0) < 900:
        return

    _last_alert[sym] = ts

    msg = (
        f"🔔 FUNDO DETECTADO\n\n"
        f"{sym}\n"
        f"Preço: {closes[-1]:.6f}\n"
        f"Abaixo da MA200\n"
        f"Compressão (velas pequenas)\n"
        f"Candle forte 2× maior\n"
        f"Rompimento pra cima\n"
    )
    await send(msg)
    print(f"[{now()}] ALERTA ENVIADO: {sym}")


# =========================
# LOOP PRINCIPAL
# =========================
async def monitor_loop():
    await send("🟢 OURO-SENTINELA INICIADO")

    print("OURO-SENTINELA RODANDO...")

    while True:
        try:
            async with aiohttp.ClientSession() as s:
                data24 = await fetch_24hr(s)

                if not data24:
                    print(f"[{now()}] Erro ao puxar 24h")
                    await asyncio.sleep(5)
                    continue

                pool = []

                for x in data24:
                    sym = x.get("symbol")
                    vol = x.get("quoteVolume")

                    if not sym or not vol:
                        continue

                    # spot usdt only
                    if not sym.endswith("USDT"):
                        continue

                    # bloqueio automático de moedas mortas
                    if any(sym.startswith(m) for m in MOEDAS_MORTAS):
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
# THREAD + FLASK
# =========================
def start_bot():
    asyncio.run(monitor_loop())

t = threading.Thread(target=start_bot, daemon=True)
t.start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
