# heatmap_monitor.py — Detector simples de FUNDO (queda + lateral + virada)
import os, asyncio, aiohttp, time, threading
from datetime import datetime

BINANCE = "https://api.binance.com"

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()

# =============================
# FILTROS DE PARES (SPOT/USDT)
# =============================
BLOQUEIO_BASE = (
    "EUR","BRL","TRY","GBP","AUD","CAD","CHF","RUB",
    "MXN","ZAR","BKRW","BVND","IDRT",
    "FDUSD","BUSD","TUSD","USDC","USDP","USDE","PAXG"
)
PADROES_LIXO = ("USD1","FUSD","BFUSD","HEDGE","BEAR","BULL","DOWN","UP","WLF","OLD")

PAIRS = []

# =============================
# PARÂMETROS (SIMPLES E DIRETOS)
# =============================
SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL", "20"))        # s entre varreduras
TF = os.getenv("TF", "5m")                                   # timeframe (5m)
KLINES_LIMIT = int(os.getenv("KLINES_LIMIT", "120"))         # velas baixadas

# 1) Queda mínima (do topo recente até o preço atual) para considerar "fundo"
DROP_LOOKBACK = int(os.getenv("DROP_LOOKBACK", "48"))        # ~4h em 5m
DROP_MIN_PCT = float(os.getenv("DROP_MIN_PCT", "7.5"))       # queda mínima (%)

# 2) Lateralização curta (range apertado)
SIDEWAYS_LOOKBACK = int(os.getenv("SIDEWAYS_LOOKBACK", "12"))  # ~1h em 5m
SIDEWAYS_MAX_RANGE_PCT = float(os.getenv("SIDEWAYS_MAX_RANGE_PCT", "1.2"))  # %

# 3) Virada: 2 velas de alta + volume acima da média simples
TURN_MIN_GREEN_CANDLES = int(os.getenv("TURN_MIN_GREEN_CANDLES", "2"))
VOL_MA_WINDOW = int(os.getenv("VOL_MA_WINDOW", "20"))
VOL_MIN_FACTOR = float(os.getenv("VOL_MIN_FACTOR", "1.20"))   # vol atual >= 1.2x média

# 4) Cooldown por par (para não spammar)
ALERT_COOLDOWN = int(os.getenv("ALERT_COOLDOWN", "1800"))     # 30 min

_last_alert = {}  # symbol -> ts do último alerta

# =============================
# UTIL
# =============================
def br_time():
    return datetime.now().strftime("%H:%M:%S")

async def tg(session, msg: str):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print(f"[{br_time()}] [TG-OFF] {msg}")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": msg}
    try:
        async with session.post(url, json=payload, timeout=10):
            pass
    except Exception as e:
        print(f"[{br_time()}] [TG-ERROR] {e}")

async def get_json(session, url, params=None, timeout=15):
    for _ in range(2):
        try:
            async with session.get(url, params=params, timeout=timeout) as r:
                return await r.json()
        except Exception:
            await asyncio.sleep(0.25)
    return None

# =============================
# PARES VÁLIDOS
# =============================
async def carregar_pairs_validos():
    async with aiohttp.ClientSession() as s:
        data = await get_json(s, f"{BINANCE}/api/v3/exchangeInfo")
        ativos = []
        for sym in data.get("symbols", []):
            if sym.get("status") != "TRADING":
                continue
            if sym.get("quoteAsset") != "USDT":
                continue
            base = sym.get("baseAsset","")
            if base in BLOQUEIO_BASE:
                continue
            if any(p in base for p in PADROES_LIXO):
                continue
            ativos.append(sym["symbol"])
        return ativos

# =============================
# KLINES
# =============================
async def fetch_klines(session, symbol, interval=TF, limit=KLINES_LIMIT):
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    return await get_json(session, f"{BINANCE}/api/v3/klines", params=params)

def sma(arr, n):
    if len(arr) < n or n <= 0: 
        return 0.0
    return sum(arr[-n:]) / float(n)

# =============================
# LÓGICA DO FUNDO (simples)
# Queda forte -> lateral curta -> virada com volume
# =============================
def detectar_fundo(kl):
    """
    kl: lista de velas no formato da Binance:
        [ open_time, open, high, low, close, volume, ... ]
    Retorna dict ou None.
    """
    if not kl or len(kl) < max(DROP_LOOKBACK, SIDEWAYS_LOOKBACK, VOL_MA_WINDOW) + 3:
        return None

    closes = [float(x[4]) for x in kl]
    highs  = [float(x[2]) for x in kl]
    lows   = [float(x[3]) for x in kl]
    vols   = [float(x[5]) for x in kl]

    last = closes[-1]

    # 1) Queda forte a partir do topo dos últimos N candles
    recent_high = max(highs[-DROP_LOOKBACK:])
    if recent_high <= 0:
        return None
    drop_pct = (recent_high - last) / recent_high * 100.0
    if drop_pct < DROP_MIN_PCT:
        return None

    # 2) Lateralização curta: range pequeno nos últimos L candles
    sw_max = max(closes[-SIDEWAYS_LOOKBACK:])
    sw_min = min(closes[-SIDEWAYS_LOOKBACK:])
    range_pct = (sw_max - sw_min) / last * 100.0 if last > 0 else 999.0
    if range_pct > SIDEWAYS_MAX_RANGE_PCT:
        return None

    # 3) Virada: últimas N velas verdes + volume atual acima da média
    green_ok = True
    for i in range(1, TURN_MIN_GREEN_CANDLES + 1):
        if closes[-i] <= closes[-i-1]:
            green_ok = False
            break
    if not green_ok:
        return None

    vol_ma = sma(vols, VOL_MA_WINDOW)
    if vol_ma <= 0:
        return None
    if vols[-1] < VOL_MIN_FACTOR * vol_ma:
        return None

    # Informações úteis
    swing_low = min(lows[-SIDEWAYS_LOOKBACK:])
    swing_high = max(highs[-SIDEWAYS_LOOKBACK:])
    return {
        "price": last,
        "drop_pct": drop_pct,
        "range_pct": range_pct,
        "swing_low": swing_low,
        "swing_high": swing_high,
        "vol_now": vols[-1],
        "vol_ma": vol_ma
    }

def _cooldown_ok(symbol):
    now = time.time()
    ts = _last_alert.get(symbol, 0)
    if now - ts >= ALERT_COOLDOWN:
        _last_alert[symbol] = now
        return True
    return False

# =============================
# MONITOR
# =============================
async def monitorar_fundos():
    global PAIRS
    if not PAIRS:
        PAIRS = await carregar_pairs_validos()
    print(f"[{br_time()}] [FUNDOS] Monitorando {len(PAIRS)} pares: {', '.join(PAIRS)}")

    async with aiohttp.ClientSession() as session:
        while True:
            for sym in PAIRS:
                try:
                    kl = await fetch_klines(session, sym)
                    if not isinstance(kl, list) or len(kl) == 0:
                        continue

                    det = detectar_fundo(kl)
                    if not det:
                        continue
                    if not _cooldown_ok(sym):
                        continue

                    base = sym.replace("USDT","")
                    msg = (
                        "🟩 FUNDO POSSÍVEL DETECTADO\n\n"
                        f"{base}\n\n"
                        f"Preço atual: {det['price']:.6f}\n"
                        f"Queda do topo recente: {det['drop_pct']:.1f}%\n"
                        f"Lateralização (range): {det['range_pct']:.2f}%\n"
                        f"Zona recente: {det['swing_low']:.6f} — {det['swing_high']:.6f}\n"
                        f"Volume agora vs média: {det['vol_now']:.0f} / {det['vol_ma']:.0f}\n\n"
                        "Leitura rápida: queda forte, range curto e virada com volume.\n"
                        "⚠️ Confirme no gráfico (5m/15m): pavio de rejeição e sequência de velas verdes."
                    )
                    await tg(session, msg)

                except Exception as e:
                    print(f"[{br_time()}] [FUNDOS-ERROR] {sym}: {e}")

            await asyncio.sleep(SCAN_INTERVAL)

# =============================
# STARTER (necessário para o main.py do Render)
# =============================
def start_heatmap_monitor():
    def runner():
        asyncio.run(monitorar_fundos())
    t = threading.Thread(target=runner, daemon=True)
    t.start()
    return t

if __name__ == "__main__":
    asyncio.run(monitorar_fundos())
