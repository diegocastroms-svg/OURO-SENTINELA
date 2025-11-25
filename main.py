import os, asyncio, aiohttp, time, threading, statistics, csv
from datetime import datetime
from flask import Flask, send_file, Response

# =========================
# CONFIGURAÇÃO
# =========================
BINANCE = "https://api.binance.com"
TOP_N = int(os.getenv("TOP_N", "30"))
SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL", "180"))
MIN_QV_USDT = float(os.getenv("MIN_QV_USDT", "15000000"))

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()
PAIRS = os.getenv("PAIRS", "").strip()

CSV_FILE = "dados_coletados.csv"

# =========================
# APP WEB
# =========================
app = Flask(__name__)
historico = []

@app.route("/")
def home():
    return "OURO-TENDÊNCIA v1.2 – Coleta + ALERTA DE FUNDO ATIVOS", 200

@app.route("/health")
def health():
    return "OK", 200


# =========================
# FERRAMENTAS
# =========================
def br_time():
    return datetime.now().strftime("%H:%M:%S")


async def get_json(session, url, params=None, timeout=15):
    for _ in range(2):
        try:
            async with session.get(url, params=params, timeout=timeout) as r:
                return await r.json()
        except Exception:
            await asyncio.sleep(0.4)
    return None


async def fetch_24hr(session):
    return await get_json(session, f"{BINANCE}/api/v3/ticker/24hr")


async def fetch_klines(session, sym, interval="5m", limit=120):
    return await get_json(
        session,
        f"{BINANCE}/api/v3/klines",
        {"symbol": sym, "interval": interval, "limit": limit},
    )


def ema(values, period):
    if not values:
        return 0.0
    k = 2 / (period + 1)
    e = values[0]
    for v in values[1:]:
        e = v * k + e * (1 - k)
    return e


def macd(values, fast=12, slow=26, signal=9):
    if len(values) < slow + signal:
        return 0.0, 0.0
    window = values[-(slow + signal):]
    ema_fast = ema(window, fast)
    ema_slow = ema(window, slow)
    macd_line = ema_fast - ema_slow
    sig = ema([macd_line] * signal, signal)
    return macd_line, macd_line - sig


def rsi(values, period=14):
    if len(values) < period + 1:
        return 50.0
    gains = [max(values[i] - values[i - 1], 0.0) for i in range(1, len(values))]
    losses = [max(values[i - 1] - values[i], 0.0) for i in range(1, len(values))]
    ag = sum(gains[-period:]) / period
    al = sum(losses[-period:]) / period or 1e-9
    return 100.0 - (100.0 / (1.0 + ag / al))


# =========================
# ALERTA DE FUNDO SIMPLES (COM SMA200)
# =========================
_last_alert = {}

def sma(values, period):
    if len(values) < period:
        return sum(values) / len(values)
    return sum(values[-period:]) / period


async def alerta_fundo(session, sym, closes):
    if len(closes) < 200:
        return

    # --- M200
    m200 = sma(closes, 200)

    # === REGRA PRINCIPAL ===
    # Só alerta se o preço estiver ABAIXO da M200 (zona de fundo real)
    if closes[-1] > m200:
        return

    ema9_now = ema(closes[-20:], 9)
    ema20_now = ema(closes[-20:], 20)

    rsi_now = rsi(closes)
    macd_line, hist = macd(closes)

    queda_forte = closes[-1] < closes[-6] * 0.985    # queda ~1.5%
    estabilizou = abs(closes[-1] - closes[-2]) <= closes[-1] * 0.003
    virada = ema9_now > ema20_now or (macd_line > 0 and hist > 0)

    if queda_forte and estabilizou and virada:
        now = time.time()
        last = _last_alert.get(sym, 0)

        if now - last > 900:  # cooldown 15 min
            _last_alert[sym] = now

            msg = (
                f"🔔 POSSÍVEL FUNDO DETECTADO\n\n"
                f"{sym}\n"
                f"Preço: {closes[-1]:.6f}\n"
                f"RSI: {rsi_now:.1f}\n"
                f"MACD virando\n"
                f"EMA9 possivelmente cruzando\n"
                f"Abaixo da M200 (região de fundo)\n\n"
                f"Queda forte + estabilização + início de reversão."
            )

            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
            try:
                await session.post(url, json={"chat_id": CHAT_ID, "text": msg})
            except:
                pass


# =========================
# LOOP PRINCIPAL – COLETA + ALERTA FUNDO
# =========================
async def monitor_loop():
    print("🚀 OURO-TENDÊNCIA + FUNDO ativo")

    while True:
        try:
            async with aiohttp.ClientSession() as s:

                all24 = await fetch_24hr(s)
                if not all24 or isinstance(all24, dict):
                    await asyncio.sleep(5)
                    continue

                allow = set(PAIRS.split(",")) if PAIRS else None
                pool = []

                for x in all24:
                    if not isinstance(x, dict):
                        continue
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

                for sym in symbols:
                    kl = await fetch_klines(s, sym, "5m", 120)
                    if not kl:
                        continue

                    closes = [float(k[4]) for k in kl]

                    # dispara alerta fundo
                    await alerta_fundo(s, sym, closes)

                await asyncio.sleep(SCAN_INTERVAL)

        except Exception as e:
            print(f"[{br_time()}] LOOP ERRO: {e}")
            await asyncio.sleep(5)


def start_background():
    def runner():
        asyncio.run(monitor_loop())
    t = threading.Thread(target=runner, daemon=True)
    t.start()
    return t


start_background()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
