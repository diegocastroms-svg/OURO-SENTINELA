import os, asyncio, aiohttp, time, threading, statistics
from datetime import datetime, timedelta
from flask import Flask

# =========================
# CONFIGURAÇÃO (versão equilibrada)
# =========================
BINANCE = "https://api.binance.com"
TOP_N = int(os.getenv("TOP_N", "30"))                  # monitora só top-30
SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL", "180")) # varredura a cada 3 min
COOLDOWN_MIN = int(os.getenv("COOLDOWN_MIN", "90"))    # 1h30 entre alertas
MIN_QV_USDT = float(os.getenv("MIN_QV_USDT", "15000000"))  # liquidez mínima 15 M USDT
MIN_FORCE = int(os.getenv("MIN_FORCE", "90"))          # força mínima exigida
BOOK_MIN_BUY = float(os.getenv("BOOK_MIN_BUY", "1.55"))    # book comprador forte
BOOK_MIN_SELL = float(os.getenv("BOOK_MIN_SELL", "0.65"))  # book vendedor forte

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()
PAIRS = os.getenv("PAIRS", "").strip()

# =========================
# APP WEB (Render health)
# =========================
app = Flask(__name__)

@app.route("/")
def home():
    return "OURO-TENDÊNCIA v1.2 – Equilibrado", 200

@app.route("/health")
def health():
    return "OK", 200

# =========================
# UTILITÁRIOS
# =========================
def br_time():
    return datetime.now().strftime("%H:%M:%S")

async def send_telegram(message: str):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    async with aiohttp.ClientSession() as s:
        await s.post(url, data={"chat_id": CHAT_ID, "text": message})

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
# AVALIAÇÃO DE TENDÊNCIA
# =========================
def calc_force(ema9, ema20, macd_line, hist, rsi_val, ratio):
    score = 0
    if ema9 > ema20:
        score += 15
    if macd_line > 0 and hist > 0:
        score += 25
    if 45 < rsi_val < 65:
        score += 10
    if ratio >= BOOK_MIN_BUY:
        score += 50
    if ema9 < ema20 and macd_line < 0 and ratio <= BOOK_MIN_SELL:
        score = max(score, 80)
    return min(score, 100)

def decide_direction(ema9, ema20, macd_line):
    if ema9 > ema20 and macd_line > 0:
        return "alta"
    if ema9 < ema20 and macd_line < 0:
        return "baixa"
    return "neutra"

def build_alert(sym, direction, force, ema9, ema20, macd_line, hist, rsi_val, ratio, vol_mult):
    hora = br_time()
    seta = "🔺" if direction == "alta" else "🔻" if direction == "baixa" else "⚪"
    tend = ("Tendência de Alta Confirmada" if direction == "alta"
            else "Tendência de Baixa Confirmada" if direction == "baixa"
            else "Tendência em Formação")
    insight = ("fluxo comprador muito forte — estrutura sólida"
               if direction == "alta"
               else "fluxo vendedor dominante — pressão de baixa"
               if direction == "baixa"
               else "monitorar aproximação de cruzamento")
    return (f"🟢🟢🟢🟢\n"
            f"📊 [OURO-TENDÊNCIA | Sinal Estratégico]\n\n"
            f"Ativo: {sym}\n"
            f"Direção: {seta} {tend}\n"
            f"Força: {force:.0f}% (base técnica + fluxo)\n\n"
            f"📈 Indicadores:\n"
            f"• EMA9 {'>' if ema9 > ema20 else '<'} EMA20\n"
            f"• MACD {'+' if macd_line > 0 else '-'} | Hist {'↑' if hist > 0 else '↓'}\n"
            f"• RSI {rsi_val:.1f}\n"
            f"• Book {ratio:.2f}× {'comprador' if ratio >= 1 else 'vendedor'} | Vol 5m {vol_mult:.1f}×\n\n"
            f"🕒 Horário: {hora} (Brasília)\n──────────────\n"
            f"📩 Insight: {insight}.")

# =========================
# LOOP PRINCIPAL
# =========================
cooldown = {}

async def scan_once():
    async with aiohttp.ClientSession() as s:
        all24 = await fetch_24hr(s)
        allow = set(PAIRS.split(",")) if PAIRS else None
        pool = [(x["symbol"], float(x["quoteVolume"])) for x in all24
                if x["symbol"].endswith("USDT")
                and (not allow or x["symbol"] in allow)
                and float(x["quoteVolume"]) >= MIN_QV_USDT]
        symbols = [s for s, _ in sorted(pool, key=lambda t: t[1], reverse=True)[:TOP_N]]
        results = []
        for sym in symbols:
            try:
                kl, dp = await asyncio.gather(fetch_klines(s, sym, "5m", 120),
                                              fetch_depth(s, sym, 40))
                closes = [float(k[4]) for k in kl]
                vols = [float(k[5]) for k in kl]
                ema9 = ema(closes[-40:], 9)
                ema20 = ema(closes[-40:], 20)
                macd_line, hist = macd(closes)
                rsi_val = rsi(closes)
                base = statistics.median(vols[-11:-1]) or 1
                vol_mult = vols[-1] / base
                ratio = book_ratio(dp)
                direction = decide_direction(ema9, ema20, macd_line)
                force = calc_force(ema9, ema20, macd_line, hist, rsi_val, ratio)
                ok_high = direction == "alta" and ratio >= BOOK_MIN_BUY and force >= MIN_FORCE
                ok_low = direction == "baixa" and ratio <= BOOK_MIN_SELL and force >= MIN_FORCE
                if ok_high or ok_low:
                    results.append((sym, direction, force, ema9, ema20, macd_line, hist, rsi_val, ratio, vol_mult))
                await asyncio.sleep(0.05)
            except:
                pass
        now = datetime.utcnow()
        msgs = []
        for r in results:
            sym = r[0]
            nxt = cooldown.get(sym, datetime.min)
            if now < nxt:
                continue
            msgs.append(build_alert(*r))
            cooldown[sym] = now + timedelta(minutes=COOLDOWN_MIN)
        for m in msgs[:3]:
            print(m.replace("\n", " | "))
            await send_telegram(m)
            await asyncio.sleep(0.3)

async def monitor_loop():
    print("🚀 OURO-TENDÊNCIA v1.2 equilibrado ativo.")
    while True:
        try:
            await scan_once()
            await asyncio.sleep(SCAN_INTERVAL)
        except Exception as e:
            print("⚠️ Erro:", e)
            await asyncio.sleep(5)

if __name__ == "__main__":
    threading.Thread(target=lambda: app.run(host="0.0.0.0",
                                            port=int(os.getenv("PORT", 10000)))).start()
    time.sleep(2)
    asyncio.run(monitor_loop())
