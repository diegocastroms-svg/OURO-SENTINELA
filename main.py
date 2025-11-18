import os, asyncio, aiohttp, time, threading, statistics
from datetime import datetime, timedelta
from flask import Flask

# =========================
# CONFIGURAÇÃO (versão equilibrada)
# =========================
BINANCE = "https://api.binance.com"
TOP_N = int(os.getenv("TOP_N", "30"))                  # monitora só top-30
SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL", "180")) # varredura a cada 3 min
COOLDOWN_MIN = int(os.getenv("COOLDOWN_MIN", "90"))    # (não usado mais p/ alerta)
MIN_QV_USDT = float(os.getenv("MIN_QV_USDT", "15000000"))  # liquidez mínima 15 M USDT
MIN_FORCE = int(os.getenv("MIN_FORCE", "90"))          # (não usado mais p/ alerta)
BOOK_MIN_BUY = float(os.getenv("BOOK_MIN_BUY", "1.55"))    # book comprador forte
BOOK_MIN_SELL = float(os.getenv("BOOK_MIN_SELL", "0.65"))  # book vendedor forte

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()
PAIRS = os.getenv("PAIRS", "").strip()

# arquivos de monitoramento
LOG_FILE = os.getenv("LOG_FILE", "monitor_ouro_tendencia.log")
CSV_FILE = os.getenv("CSV_FILE", "monitor_ouro_tendencia.csv")

# =========================
# APP WEB (Render health)
# =========================
app = Flask(__name__)

@app.route("/")
def home():
    return "OURO-TENDÊNCIA v1.2 – Equilibrado (MODO MONITORAMENTO)", 200

@app.route("/health")
def health():
    return "OK", 200

# =========================
# UTILITÁRIOS
# =========================
def br_time():
    return datetime.now().strftime("%H:%M:%S")

async def send_telegram(message: str):
    # modo monitoramento: não enviar nada
    return

async def get_json(session, url, params=None, timeout=15):
    for _ in range(2):
        try:
            async with session.get(url, params=params, timeout=timeout) as r:
                return await r.json()
        except:
            await asyncio.sleep(0.4)
    raise RuntimeError(f"Erro em {url}")

async def fetch_24hr(session):
    return await get_json(session, f"{BINANCE}/api/v3/ticker/24hr")

async def fetch_klines(session, sym, interval="5m", limit=120):
    return await get_json(session, f"{BINANCE}/api/v3/klines",
                          {"symbol": sym, "interval": interval, "limit": limit})

async def fetch_depth(session, sym, limit=40):
    return await get_json(session, f"{BINANCE}/api/v3/depth", {"symbol": sym, "limit": limit})

# =========================
# INDICADORES
# =========================
def ema(values, period):
    k = 2 / (period + 1)
    e = values[0]
    for v in values[1:]:
        e = v * k + e * (1 - k)
    return e

def macd(values, fast=12, slow=26, signal=9):
    if len(values) < slow + signal:
        return 0, 0
    ema_fast = ema(values[-(slow + signal):], fast)
    ema_slow = ema(values[-(slow + signal):], slow)
    macd_line = ema_fast - ema_slow
    sig = ema([macd_line] * signal, signal)
    return macd_line, macd_line - sig

def rsi(values, period=14):
    if len(values) < period + 1:
        return 50
    gains = [max(values[i] - values[i-1], 0) for i in range(1, len(values))]
    losses = [max(values[i-1] - values[i], 0) for i in range(1, len(values))]
    ag = sum(gains[-period:]) / period
    al = sum(losses[-period:]) / period or 1e-9
    return 100 - (100 / (1 + ag / al))

def book_ratio(depth):
    def power(levels):
        return sum(float(p) * float(q) for p, q in levels[:20])
    b = power(depth.get("bids", []))
    a = power(depth.get("asks", []))
    return b / a if a > 0 else 0

# =========================
# LOOP PRINCIPAL (MODO MONITOR)
# =========================
cooldown = {}  # mantido, mas não usado (pra não mexer na estrutura)

async def scan_once():
    async with aiohttp.ClientSession() as s:
        all24 = await fetch_24hr(s)
        allow = set(PAIRS.split(",")) if PAIRS else None

        pool = [(x["symbol"], float(x["quoteVolume"])) for x in all24
                if x["symbol"].endswith("USDT")
                and (not allow or x["symbol"] in allow)
                and float(x["quoteVolume"]) >= MIN_QV_USDT]

        symbols = [s for s, _ in sorted(pool, key=lambda t: t[1], reverse=True)[:TOP_N]]

        # garante cabeçalho do CSV na primeira vez
        try:
            if not os.path.exists(CSV_FILE):
                with open(CSV_FILE, "w", encoding="utf-8") as cf:
                    cf.write("hora,symbol,ema9,ema20,macd,hist,rsi,vol_mult,book_ratio\n")
        except:
            pass

        for sym in symbols:
            try:
                kl, dp = await asyncio.gather(
                    fetch_klines(s, sym, "5m", 120),
                    fetch_depth(s, sym, 40)
                )

                closes = [float(k[4]) for k in kl]
                vols = [float(k[5]) for k in kl]

                ema9 = ema(closes[-40:], 9)
                ema20 = ema(closes[-40:], 20)
                macd_line, hist = macd(closes)
                rsi_val = rsi(closes)

                base = statistics.median(vols[-11:-1]) or 1
                vol_mult = vols[-1] / base
                ratio = book_ratio(dp)

                # ================================
                # MONITORAMENTO: LOG + CSV
                # ================================
                hora = br_time()
                log_line = (
                    f"[{hora}] {sym} | 5m | "
                    f"EMA9={ema9:.6f} | EMA20={ema20:.6f} | "
                    f"MACD={macd_line:.6f} | HIST={hist:.6f} | "
                    f"RSI={rsi_val:.1f} | VOLx={vol_mult:.2f} | BOOK={ratio:.2f}"
                )

                # imprime no console (Render logs)
                print(log_line)

                # grava em arquivo .log
                try:
                    with open(LOG_FILE, "a", encoding="utf-8") as lf:
                        lf.write(log_line + "\n")
                except:
                    pass

                # grava em CSV
                try:
                    with open(CSV_FILE, "a", encoding="utf-8") as cf:
                        cf.write(
                            f"{hora},{sym},{ema9:.6f},{ema20:.6f},"
                            f"{macd_line:.6f},{hist:.6f},{rsi_val:.1f},"
                            f"{vol_mult:.4f},{ratio:.4f}\n"
                        )
                except:
                    pass

                await asyncio.sleep(0.05)
            except Exception as e:
                print("Erro scan_once:", e)

async def monitor_loop():
    print("🚀 OURO-TENDÊNCIA v1.2 equilibrado ativo (MODO MONITORAMENTO, sem alertas).")
    while True:
        try:
            await scan_once()
            await asyncio.sleep(SCAN_INTERVAL)
        except Exception as e:
            print("⚠️ Erro:", e)
            await asyncio.sleep(5)

if __name__ == "__main__":
    threading.Thread(
        target=lambda: app.run(
            host="0.0.0.0",
            port=int(os.getenv("PORT", 10000))
        )
    ).start()
    time.sleep(2)
    asyncio.run(monitor_loop())
