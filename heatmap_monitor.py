# heatmap_monitor.py — Monitor de clusters (heatmap) com alerta único e dinâmico
import os, asyncio, aiohttp, time, threading
from datetime import datetime

BINANCE = "https://api.binance.com"

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()


# ============================================
# BOTÕES LIGAR/DESLIGAR ALERTAS
# ============================================
ALERTA_UP = True        # True = ligado / False = desligado
ALERTA_DOWN = False     # True = ligado / False = desligado (DESLIGADO por padrão)


# ============================================
# ACEITAR SOMENTE PARES SPOT USDT
# E BLOQUEAR FIAT / STABLES FRACAS / TOKENS LIXO
# ============================================
BLOQUEIO_BASE = (
    "EUR", "BRL", "TRY", "GBP", "AUD", "CAD", "CHF", "RUB",
    "MXN", "ZAR", "BKRW", "BVND", "IDRT",
    "FDUSD", "BUSD", "TUSD", "USDC", "USDP", "USDE", "PAXG"
)

PADROES_LIXO = (
    "USD1", "FUSD", "BFUSD",
    "HEDGE", "BEAR", "BULL", "DOWN", "UP",
    "WLF", "OLD"
)


async def carregar_pairs_validos():
    async with aiohttp.ClientSession() as s:
        url = f"{BINANCE}/api/v3/exchangeInfo"
        r = await s.get(url)
        data = await r.json()

        ativos = []
        for sym in data["symbols"]:

            if sym["status"] != "TRADING":
                continue

            if sym["quoteAsset"] != "USDT":
                continue

            base = sym["baseAsset"]

            if base in BLOQUEIO_BASE:
                continue

            if any(p in base for p in PADROES_LIXO):
                continue

            ativos.append(sym["symbol"])

        return ativos


PAIRS = []


# CONFIG
HEATMAP_INTERVAL = int(os.getenv("HEATMAP_INTERVAL", "60"))
HEATMAP_MAX_DIST_PCT = float(os.getenv("HEATMAP_MAX_DIST_PCT", "0.10"))
HEATMAP_MIN_CLUSTER_USD = float(os.getenv("HEATMAP_MIN_CLUSTER_USD", "150000"))
HEATMAP_MIN_DOMINANCE_RATIO = float(os.getenv("HEATMAP_MIN_DOMINANCE_RATIO", "1.3"))
HEATMAP_ALERT_COOLDOWN = int(os.getenv("HEATMAP_ALERT_COOLDOWN", "900"))

_last_alert = {}

# ============================================
# CONFIRMAÇÃO DUPLA
# ============================================
_confirmacao = {}


def br_time():
    return datetime.now().strftime("%H:%M:%S")


async def tg(session, msg):
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
            await asyncio.sleep(0.3)
    return None


async def fetch_depth(session, sym, limit=100):
    return await get_json(
        session,
        f"{BINANCE}/api/v3/depth",
        {"symbol": sym, "limit": limit},
    )


def analisar_book(depth, mid_price):
    asks = [(float(p), float(q)) for p, q in depth["asks"]]
    bids = [(float(p), float(q)) for p, q in depth["bids"]]

    max_up, max_down = None, None
    max_dist = mid_price * HEATMAP_MAX_DIST_PCT

    for p, q in asks:
        if p <= mid_price: continue
        if p - mid_price > max_dist: continue
        notional = p * q
        if notional < HEATMAP_MIN_CLUSTER_USD: continue
        if (max_up is None) or (notional > max_up["notional"]):
            max_up = {"price": p, "notional": notional}

    for p, q in bids:
        if p >= mid_price: continue
        if mid_price - p > max_dist: continue
        notional = p * q
        if notional < HEATMAP_MIN_CLUSTER_USD: continue
        if (max_down is None) or (notional > max_down["notional"]):
            max_down = {"price": p, "notional": notional}

    return {"cluster_up": max_up, "cluster_down": max_down}


def decidir_direcao(info):
    up = info["cluster_up"]
    down = info["cluster_down"]

    if not up and not down:
        return {"side": "FLAT", "dominance": 0}

    if up and not down:
        return {"side": "UP", "dominance": 1}

    if down and not up:
        return {"side": "DOWN", "dominance": 1}

    up_n = up["notional"]
    down_n = down["notional"]

    if up_n > down_n * HEATMAP_MIN_DOMINANCE_RATIO:
        return {"side": "UP", "dominance": up_n / (up_n + down_n)}

    if down_n > up_n * HEATMAP_MIN_DOMINANCE_RATIO:
        return {"side": "DOWN", "dominance": down_n / (up_n + down_n)}

    return {"side": "FLAT", "dominance": 0}



def _pode_alertar(symbol, side):
    now = time.time()
    info = _last_alert.get(symbol)

    if not info:
        _last_alert[symbol] = {"ts": now, "side": side}
        return True

    if info["side"] != side:
        _last_alert[symbol] = {"ts": now, "side": side}
        return True

    if now - info["ts"] < HEATMAP_ALERT_COOLDOWN:
        return False

    _last_alert[symbol] = {"ts": now, "side": side}
    return True



async def monitorar_heatmap():
    global PAIRS

    if not PAIRS:
        PAIRS = await carregar_pairs_validos()

    print(f"[{br_time()}] [HEATMAP] Ativo para: {', '.join(PAIRS)}")

    async with aiohttp.ClientSession() as session:
        while True:
            for sym in PAIRS:
                try:
                    depth = await fetch_depth(session, sym)
                    if not depth: continue

                    best_bid = float(depth["bids"][0][0])
                    best_ask = float(depth["asks"][0][0])
                    mid = (best_bid + best_ask) / 2

                    info = analisar_book(depth, mid)
                    dec = decidir_direcao(info)
                    side = dec["side"]
                    dom = dec["dominance"]

                    # BOTÕES ON/OFF
                    if side == "UP" and not ALERTA_UP:
                        continue
                    if side == "DOWN" and not ALERTA_DOWN:
                        continue
                    if side == "FLAT":
                        continue

                    if dom < 0.55:
                        continue

                    cluster = info["cluster_up"] if side == "UP" else info["cluster_down"]
                    if not cluster:
                        continue

                    # ============================================
                    # CONFIRMAÇÃO DUPLA
                    # ============================================
                    prev = _confirmacao.get(sym)

                    if prev != side:
                        _confirmacao[sym] = side
                        continue  # primeira confirmação

                    # segunda confirmação → passa
                    _confirmacao[sym] = side

                    # ============================================
                    # COOLDOWN
                    # ============================================
                    if not _pode_alertar(sym, side):
                        continue

                    alvo = cluster["price"]
                    notional = cluster["notional"]

                    if side == "UP":
                        msg = (
                            f"🔥 HEATMAP {sym} — ALTA FORTE\n"
                            f"Preço: {mid:.6f} → Cluster: {alvo:.6f}\n"
                            f"Notional: ~{notional:,.0f} USDT | Dom: {dom*100:.1f}%\n\n"
                            f"Confirmação dupla concluída — Alta REAL"
                        )
                    else:
                        msg = (
                            f"⚠️ HEATMAP {sym} — QUEDA FORTE\n"
                            f"Preço: {mid:.6f} → Cluster: {alvo:.6f}\n"
                            f"Notional: ~{notional:,.0f} USDT | Dom: {dom*100:.1f}%\n\n"
                            f"Confirmação dupla concluída — Queda REAL"
                        )

                    await tg(session, msg)

                except Exception as e:
                    print(f"[{br_time()}] [HEATMAP-ERROR] {sym}: {e}")

            await asyncio.sleep(HEATMAP_INTERVAL)



def start_heatmap_monitor():
    def runner():
        asyncio.run(monitorar_heatmap())

    t = threading.Thread(target=runner, daemon=True)
    t.start()
    return t



if __name__ == "__main__":
    asyncio.run(monitorar_heatmap())
