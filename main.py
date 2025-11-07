import os, asyncio, aiohttp, time, threading, statistics
from datetime import datetime, timedelta
from flask import Flask

# =========================
# CONFIGURAÇÃO (via Env)
# =========================
BINANCE = "https://api.binance.com"
TOP_N = int(os.getenv("TOP_N", "60"))                 # quantos pares (USDT) por volume 24h
SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL", "120"))# segundos entre varreduras
COOLDOWN_MIN = int(os.getenv("COOLDOWN_MIN", "45"))   # min sem repetir alerta daquele par
MIN_QV_USDT = float(os.getenv("MIN_QV_USDT", "5000000"))  # liquidez mínima 24h (USDT)

# Critérios de confirmação de TENDÊNCIA
MIN_FORCE = int(os.getenv("MIN_FORCE", "70"))         # força mínima (0–100) para alertar
BOOK_MIN_BUY = float(os.getenv("BOOK_MIN_BUY", "1.30"))   # ratio bids/asks mínimo para alta
BOOK_MIN_SELL = float(os.getenv("BOOK_MIN_SELL", "0.77")) # ratio bids/asks máximo para baixa

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()
PAIRS = os.getenv("PAIRS", "").strip()  # se vazio, o scanner decide pelo Top N por volume

# =========================
# APP WEB (saúde no Render)
# =========================
app = Flask(__name__)

@app.route("/")
def home():
    return "OURO-TENDÊNCIA v1.0 – Online", 200

@app.route("/health")
def health():
    return "OK", 200

# =========================
# UTILITÁRIOS
# =========================
def br_time():
    # Apenas rótulo visual; Render roda em UTC. Ajuste visual local.
    return datetime.now().strftime("%H:%M:%S")

def fmt_m(v):
    return f"{v/1_000_000:.1f}M"

async def send_telegram(message: str):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("⚠️ Faltando TELEGRAM_TOKEN ou CHAT_ID")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message}
    async with aiohttp.ClientSession() as s:
        await s.post(url, data=payload)

async def get_json(session, url, params=None, timeout=15):
    for _ in range(2):
        try:
            async with session.get(url, params=params, timeout=timeout) as r:
                return await r.json()
        except Exception:
            await asyncio.sleep(0.4)
    raise RuntimeError(f"Falha ao obter {url}")

# =========================
# DATA: Binance endpoints
# =========================
async def fetch_24hr(session):
    return await get_json(session, f"{BINANCE}/api/v3/ticker/24hr")

async def fetch_klines(session, symbol, interval="5m", limit=120):
    return await get_json(session, f"{BINANCE}/api/v3/klines",
                          {"symbol": symbol, "interval": interval, "limit": limit})

async def fetch_depth(session, symbol, limit=40):
    return await get_json(session, f"{BINANCE}/api/v3/depth",
                          {"symbol": symbol, "limit": limit})

# =========================
# INDICADORES (5m)
# =========================
def ema(values, period):
    if not values: return 0.0
    k = 2 / (period + 1)
    e = float(values[0])
    for v in values[1:]:
        e = float(v) * k + e * (1 - k)
    return e

def macd(values, fast=12, slow=26, signal=9):
    if len(values) < slow + signal:  # segurança
        return 0.0, 0.0
    # Para suavizar, calcule EMAs na cauda necessária
    slow_slice = values[-(slow+signal+3):]
    ema_fast = ema(slow_slice, fast)
    ema_slow = ema(slow_slice, slow)
    macd_line = ema_fast - ema_slow
    sig = ema([ema_fast - ema_slow for _ in range(signal)], signal)  # aproximação leve
    hist = macd_line - sig
    return macd_line, hist

def rsi(values, period=14):
    if len(values) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(values)):
        diff = values[i] - values[i-1]
        gains.append(max(diff, 0))
        losses.append(-min(diff, 0))
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period or 1e-9
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def book_ratio(depth):
    """Soma preço*qtd nos 20 níveis de bids/asks."""
    def power(levels):
        total = 0.0
        for p, q in levels[:20]:
            total += float(p) * float(q)
        return total
    b = power(depth.get("bids", []))
    a = power(depth.get("asks", []))
    if a <= 0: return 0.0
    return b / a

# =========================
# AVALIAÇÃO DE TENDÊNCIA
# =========================
def calc_force(ema9, ema20, macd_line, hist, rsi_val, ratio):
    score = 0
    # direção/estrutura
    if ema9 > ema20: score += 25
    if macd_line > 0 and hist > 0: score += 25
    if 45 < rsi_val < 65: score += 20
    # fluxo (book)
    if ratio >= BOOK_MIN_BUY: score += 30
    if ema9 < ema20 or macd_line < 0:
        # em baixa também pode haver força, mas aqui focamos em compras
        if ratio <= BOOK_MIN_SELL: score = max(score, 65)  # concede força para baixa confirmada
    return min(score, 100)

def decide_direction(ema9, ema20, macd_line):
    if ema9 > ema20 and macd_line > 0: return "alta"
    if ema9 < ema20 and macd_line < 0: return "baixa"
    return "neutra"

def build_alert(symbol, direction, force, ema9, ema20, macd_line, hist, rsi_val, ratio, vol_mult):
    hora = br_time()
    seta = "🔺" if direction == "alta" else "🔻" if direction == "baixa" else "⚪"
    tend = ("Tendência de Alta Confirmada" if direction=="alta"
            else "Tendência de Baixa Confirmada" if direction=="baixa"
            else "Tendência em Formação")
    insight = ("fluxo comprador consistente — manter enquanto RSI < 70 e EMA9>EMA20"
               if direction=="alta" and force>=MIN_FORCE else
               "fluxo vendedor consistente — manter enquanto EMA9<EMA20"
               if direction=="baixa" and force>=MIN_FORCE else
               "estrutura ganhando força — aguardar confirmação")
    text = (
f"🟢🟢🟢🟢\n"
f"📊 [OURO-TENDÊNCIA | Sinal Estratégico]\n\n"
f"Ativo: {symbol}\n"
f"Direção: {seta} {tend}\n"
f"Força: {force:.0f}% (base técnica + fluxo)\n\n"
f"📈 Indicadores:\n"
f"• EMA9 {'>' if ema9>ema20 else '<'} EMA20\n"
f"• MACD {'+' if macd_line>0 else '-'} | Hist {'↑' if hist>0 else '↓'}\n"
f"• RSI {rsi_val:.1f}\n"
f"• Book {ratio:.2f}× {'comprador' if ratio>=1 else 'vendedor'} | Vol 5m {vol_mult:.1f}×\n\n"
f"🕒 Horário: {hora} (Brasília)\n"
f"──────────────\n"
f"📩 Insight: {insight}."
)
    return text

# =========================
# LOOP PRINCIPAL
# =========================
cooldown = {}  # symbol -> datetime (UTC) próximo permitido

async def scan_once():
    async with aiohttp.ClientSession() as session:
        # 1) Seleção de pares
        all24 = await fetch_24hr(session)
        allow = set(PAIRS.split(",")) if PAIRS else None

        pool = []
        for x in all24:
            s = x.get("symbol", "")
            if not s.endswith("USDT"): 
                continue
            if allow and s not in allow:
                continue
            qv = float(x.get("quoteVolume", 0.0))
            if qv < MIN_QV_USDT:
                continue
            pool.append((s, qv))

        # ordena por volume e pega TOP_N
        symbols = [s for s, _ in sorted(pool, key=lambda t: t[1], reverse=True)[:TOP_N]]

        results = []
        for sym in symbols:
            try:
                kl_5m, depth = await asyncio.gather(
                    fetch_klines(session, sym, "5m", 120),
                    fetch_depth(session, sym, 40)
                )
                closes = [float(k[4]) for k in kl_5m]
                vols   = [float(k[5]) for k in kl_5m]
                if len(closes) < 40: 
                    await asyncio.sleep(0.03); 
                    continue

                # Indicadores
                ema9 = ema(closes[-40:], 9)
                ema20 = ema(closes[-40:], 20)
                macd_line, hist = macd(closes)
                rsi_val = rsi(closes)

                # Volume 5m vs mediana 10 velas
                base = statistics.median(vols[-11:-1]) if len(vols) >= 12 else (sum(vols[-6:-1])/5 if len(vols)>=6 else 1)
                vol_mult = vols[-1] / (base or 1)

                # Book ratio
                ratio = book_ratio(depth)

                # Decisão e força
                direction = decide_direction(ema9, ema20, macd_line)
                force = calc_force(ema9, ema20, macd_line, hist, rsi_val, ratio)

                # Regras de disparo
                ok_high = (direction == "alta" and ratio >= BOOK_MIN_BUY and force >= MIN_FORCE)
                ok_low  = (direction == "baixa" and ratio <= BOOK_MIN_SELL and force >= MIN_FORCE)

                if ok_high or ok_low:
                    results.append((sym, direction, force, ema9, ema20, macd_line, hist, rsi_val, ratio, vol_mult))

                await asyncio.sleep(0.05)  # respeito leve ao rate limit
            except Exception as e:
                # silencioso para não travar a varredura
                pass

        # 3) Envio com cooldown
        now = datetime.utcnow()
        msgs = []
        for (sym, direction, force, ema9, ema20, macd_line, hist, rsi_val, ratio, vol_mult) in results:
            next_ok = cooldown.get(sym, datetime.min)
            if now < next_ok:
                continue
            msg = build_alert(sym, direction, force, ema9, ema20, macd_line, hist, rsi_val, ratio, vol_mult)
            msgs.append(msg)
            cooldown[sym] = now + timedelta(minutes=COOLDOWN_MIN)

        # Limite de mensagens por ciclo para evitar flood
        for m in msgs[:5]:
            print(m.replace("\n", " | "))
            await send_telegram(m)
            await asyncio.sleep(0.25)

async def monitor_loop():
    print("🚀 OURO-TENDÊNCIA v1.0 iniciado.")
    while True:
        try:
            await scan_once()
            await asyncio.sleep(SCAN_INTERVAL)
        except Exception as e:
            print("⚠️ Erro no loop:", e)
            await asyncio.sleep(5)

# ========= ENTRYPOINT (Render) =========
if __name__ == "__main__":
    threading.Thread(target=lambda: app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))).start()
    time.sleep(2)
    asyncio.run(monitor_loop())
