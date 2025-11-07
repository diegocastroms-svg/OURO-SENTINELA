import os, asyncio, aiohttp, time, threading
from datetime import datetime, timedelta
from flask import Flask

# =========================
# CONFIGURAÇÃO (padrões)
# =========================
BINANCE = "https://api.binance.com"
TOP_N = int(os.getenv("TOP_N", "60"))               # quantos pares analisar por volume
SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL", "60"))  # segundos entre varreduras
COOLDOWN_MIN = int(os.getenv("COOLDOWN_MIN", "30"))    # min sem repetir alerta do mesmo par

# Critérios de PRÉ-PUMP (ajuste fino aqui)
VOL_SPIKE_MULT = float(os.getenv("VOL_SPIKE_MULT", "3.0"))     # volume da vela atual vs mediana 20
BOOK_IMBALANCE = float(os.getenv("BOOK_IMBALANCE", "1.6"))     # soma(bids)/soma(asks) (top 20 níveis)
GAP_PCT = float(os.getenv("GAP_PCT", "0.35")) / 100.0          # gap mínimo entre close→open (1m)
MIN_QV_USDT = float(os.getenv("MIN_QV_USDT", "2000000"))       # filtro de liquidez (24h quote volume)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()
PAIRS = os.getenv("PAIRS", "").strip()  # se vazio, analisa todos os símbolos

# =========================
# APP WEB (Render health)
# =========================
app = Flask(__name__)

@app.route("/")
def home():
    return "OURO SENTINELA – Pré-pump ativo", 200

@app.route("/health")
def health():
    return "OK", 200


# =========================
# UTILITÁRIOS
# =========================
async def send_telegram(text: str):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("⚠️ Faltando TELEGRAM_TOKEN ou CHAT_ID")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text}
    async with aiohttp.ClientSession() as s:
        await s.post(url, data=payload)

def fmt_m(val):
    return f"{val/1_000_000:.1f}M"

def now_hms():
    return datetime.now().strftime("%H:%M:%S")


# =========================
# BINANCE CLIENT (aiohttp)
# =========================
async def get_json(session, url, params=None):
    for _ in range(2):
        try:
            async with session.get(url, params=params, timeout=15) as r:
                return await r.json()
        except Exception as e:
            await asyncio.sleep(0.5)
    raise RuntimeError(f"Erro ao buscar {url}")

async def fetch_24hr(session):
    return await get_json(session, f"{BINANCE}/api/v3/ticker/24hr")

async def fetch_klines_1m(session, symbol, limit=60):
    return await get_json(session, f"{BINANCE}/api/v3/klines", {"symbol": symbol, "interval": "1m", "limit": limit})

async def fetch_depth(session, symbol, limit=20):
    return await get_json(session, f"{BINANCE}/api/v3/depth", {"symbol": symbol, "limit": limit})


# =========================
# LÓGICA DE SINAIS PRÉ-PUMP
# =========================
from statistics import median

def volume_spike(klines):
    """Retorna (bool, mult, vol_atual) comparando volume atual vs mediana das últimas 20 velas."""
    vols = [float(k[5]) for k in klines[:-1]]  # exclui a vela em formação
    if len(vols) < 20:
        return False, 0.0, 0.0
    base = median(vols[-20:])
    atual = float(klines[-1][5])
    if base <= 0:
        return False, 0.0, atual
    mult = atual / base
    return mult >= VOL_SPIKE_MULT, mult, atual

def gap_detect(klines):
    """Gap entre close anterior e open atual."""
    if len(klines) < 2: 
        return False, 0.0
    prev_close = float(klines[-2][4])
    curr_open = float(klines[-1][1])
    if prev_close == 0: 
        return False, 0.0
    pct = abs(curr_open - prev_close) / prev_close
    return pct >= GAP_PCT, pct

def book_imbalance(depth):
    """Soma preço*quantidade top20 em bids/asks e calcula razão."""
    def side_power(levels):
        total = 0.0
        for p, q in levels[:20]:
            total += float(p) * float(q)
        return total
    b = side_power(depth.get("bids", []))
    a = side_power(depth.get("asks", []))
    if a <= 0:
        return False, 0.0
    ratio = b / a
    return ratio >= BOOK_IMBALANCE, ratio


# =========================
# LOOP PRINCIPAL
# =========================
cooldown = {}  # symbol -> datetime próximo permitido

async def scan_once():
    async with aiohttp.ClientSession() as session:
        # 1) pega 24h para ordenar por volume e filtrar liquidez
        all24 = await fetch_24hr(session)

        # filtra por USDT e liquidez mínima; aplica PAIRS se fornecido
        selected = []
        allow = set(PAIRS.split(",")) if PAIRS else None
        for x in all24:
            s = x.get("symbol", "")
            if not s.endswith("USDT"): 
                continue
            if allow and s not in allow:
                continue
            qv = float(x.get("quoteVolume", 0.0))
            if qv < MIN_QV_USDT:
                continue
            selected.append((s, qv))
        # top N por volume
        selected = [s for s, _ in sorted(selected, key=lambda t: t[1], reverse=True)[:TOP_N]]

        # 2) para cada símbolo, pega klines e depth e calcula sinais
        results = []
        for sym in selected:
            try:
                kl, dp = await asyncio.gather(
                    fetch_klines_1m(session, sym, 60),
                    fetch_depth(session, sym, 50)
                )
                ok_vol, mult, vol_atual = volume_spike(kl)
                ok_gap, gapv = gap_detect(kl)
                ok_book, ratio = book_imbalance(dp)
                if ok_vol or ok_gap or ok_book:
                    results.append((sym, ok_vol, mult, vol_atual, ok_gap, gapv, ok_book, ratio))
                await asyncio.sleep(0.05)  # respeito leve ao rate limit
            except Exception as e:
                # silencia erros pontuais para não travar o loop
                pass

        # 3) envia alertas com cooldown
        now = datetime.utcnow()
        msgs = []
        for (sym, ok_vol, mult, vol_atual, ok_gap, gapv, ok_book, ratio) in results:
            next_ok = cooldown.get(sym, datetime.min)
            if now < next_ok:
                continue
            sinais = []
            if ok_vol:  sinais.append(f"Volume ↑ {mult:.1f}x ({fmt_m(vol_atual)})")
            if ok_book: sinais.append(f"Book compr. {ratio:.2f}x")
            if ok_gap:  sinais.append(f"Gap 1m {gapv*100:.2f}%")
            if not sinais:
                continue
            texto = (
                "⚡ OURO SENTINELA — ALERTA PRÉ-PUMP ⚡\n"
                f"Par: {sym}\n"
                f"{' | '.join(sinais)}\n"
                f"Hora: {now_hms()}"
            )
            msgs.append(texto)
            cooldown[sym] = now + timedelta(minutes=COOLDOWN_MIN)

        # envia (limite básico: até 10 mensagens por ciclo)
        for m in msgs[:10]:
            print(m.replace("\n", " | "))
            await send_telegram(m)
            await asyncio.sleep(0.2)

async def monitor_loop():
    print("🚀 OURO SENTINELA (PRÉ-PUMP) iniciado.")
    while True:
        try:
            await scan_once()
            await asyncio.sleep(SCAN_INTERVAL)
        except Exception as e:
            print("⚠️ Erro no loop:", e)
            await asyncio.sleep(5)

# ========= INÍCIO PARA RENDER =========
if __name__ == "__main__":
    # sobe Flask numa thread e roda o loop assíncrono no main
    threading.Thread(target=lambda: app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))).start()
    time.sleep(2)
    asyncio.run(monitor_loop())
