# heatmap_monitor.py — Monitor de heatmap usando somente o book (GRÁTIS)
# Pronto para rodar junto com OURO-TENDÊNCIA v1.2

import os, asyncio, aiohttp, time, threading
from datetime import datetime

BINANCE = "https://api.binance.com"

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()

# Intervalo de varredura (30 segundos)
HEATMAP_INTERVAL = int(os.getenv("HEATMAP_INTERVAL", "30"))

# Distância máxima (em %) do preço para considerar cluster
HEATMAP_MAX_DIST_PCT = float(os.getenv("HEATMAP_MAX_DIST_PCT", "0.10"))

# Valor mínimo em USDT para cluster significativo
HEATMAP_MIN_CLUSTER_USD = float(os.getenv("HEATMAP_MIN_CLUSTER_USD", "150000"))

# Dominância mínima para considerar direção
HEATMAP_MIN_DOMINANCE_RATIO = float(os.getenv("HEATMAP_MIN_DOMINANCE_RATIO", "1.3"))

# Cooldown por moeda/direção (15 minutos)
HEATMAP_ALERT_COOLDOWN = int(os.getenv("HEATMAP_ALERT_COOLDOWN", "900"))

# Moedas que NÃO queremos monitorar
BANNED = [
    "USDT","USDC","FDUSD","BUSD","EUR","BRL","TRY","TUSD","DAI",
    "GBP","AUD","CAD","RUB","UAH"
]

_last_alert = {}


def br_time():
    return datetime.now().strftime("%H:%M:%S")


async def tg(session: aiohttp.ClientSession, msg: str):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print(f"[{br_time()}] [TG-OFF] {msg}")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": msg}
    try:
        async with session.post(url, json=payload, timeout=10) as r:
            await r.text()
    except:
        pass


async def get_json(session, url, params=None, timeout=15):
    try:
        async with session.get(url, params=params, timeout=timeout) as r:
            return await r.json()
    except:
        return None


async def fetch_depth(session, sym, limit=100):
    return await get_json(session, f"{BINANCE}/api/v3/depth", {"symbol": sym, "limit": limit})


async def fetch_24hr(session):
    return await get_json(session, f"{BINANCE}/api/v3/ticker/24hr")


def analisar_book(depth, mid_price):
    asks = [(float(p), float(q)) for p, q in depth.get("asks", [])]
    bids = [(float(p), float(q)) for p, q in depth.get("bids", [])]

    max_up = None
    max_down = None
    max_dist = mid_price * HEATMAP_MAX_DIST_PCT

    for p, q in asks:
        if p <= mid_price or p - mid_price > max_dist:
            continue
        notional = p * q
        if notional < HEATMAP_MIN_CLUSTER_USD:
            continue
        if not max_up or notional > max_up["notional"]:
            max_up = {"price": p, "notional": notional}

    for p, q in bids:
        if p >= mid_price or mid_price - p > max_dist:
            continue
        notional = p * q
        if notional < HEATMAP_MIN_CLUSTER_USD:
            continue
        if not max_down or notional > max_down["notional"]:
            max_down = {"price": p, "notional": notional}

    return {"cluster_up": max_up, "cluster_down": max_down}


def decidir_direcao(info):
    up = info["cluster_up"]
    down = info["cluster_down"]

    if not up and not down:
        return {"side": "FLAT", "dominance": 0.0}

    if up and not down:
        return {"side": "UP", "dominance": 1.0}

    if down and not up:
        return {"side": "DOWN", "dominance": 1.0}

    if up["notional"] > down["notional"] * HEATMAP_MIN_DOMINANCE_RATIO:
        dom = up["notional"] / (up["notional"] + down["notional"])
        return {"side": "UP", "dominance": dom}

    if down["notional"] > up["notional"] * HEATMAP_MIN_DOMINANCE_RATIO:
        dom = down["notional"] / (up["notional"] + down["notional"])
        return {"side": "DOWN", "dominance": dom}

    return {"side": "FLAT", "dominance": 0.0}


def pode_alertar(symbol, side):
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
    print(f"[{br_time()}] [HEATMAP] Coletando lista de moedas spot...")

    async with aiohttp.ClientSession() as session:

        data = await fetch_24hr(session)
        if not data:
            print(f"[{br_time()}] [HEATMAP] Erro ao obter lista de moedas.")
            return

        pares = []
        for d in data:
            s = d["symbol"]
            if not s.endswith("USDT"):
                continue
            base = s.replace("USDT", "")
            if base.upper() in BANNED:
                continue
            pares.append(s)

        print(f"[{br_time()}] [HEATMAP] Monitorando {len(pares)} pares spot.")

        while True:
            for symbol in pares:
                try:
                    depth = await fetch_depth(session, symbol, 100)
                    if not depth or not depth.get("bids") or not depth.get("asks"):
                        continue

                    best_bid = float(depth["bids"][0][0])
                    best_ask = float(depth["asks"][0][0])
                    mid = (best_bid + best_ask) / 2

                    info = analisar_book(depth, mid)
                    decision = decidir_direcao(info)

                    side = decision["side"]
                    dom = decision["dominance"]

                    if side in ("UP", "DOWN") and dom >= 0.55:
                        cluster = info["cluster_up"] if side == "UP" else info["cluster_down"]
                        if cluster and pode_alertar(symbol, side):

                            msg = (
                                f"{br_time()} — HEATMAP {symbol}\n"
                                f"Direção provável: {'ALTA' if side=='UP' else 'QUEDA'}\n"
                                f"Preço médio: {mid:.6f}\n"
                                f"Cluster alvo: {cluster['price']:.6f}\n"
                                f"Notional: ~{cluster['notional']:,.0f} USDT\n"
                                f"Dominância: {dom*100:.1f}%"
                            )

                            await tg(session, msg)

                except Exception as e:
                    print(f"[{br_time()}] [HEATMAP-ERROR] {symbol}: {e}")

            await asyncio.sleep(HEATMAP_INTERVAL)


# ============================================================
# FUNÇÃO PARA STARTAR O HEATMAP (necessário no main.py)
# ============================================================

def start_heatmap_monitor():
    def runner():
        asyncio.run(monitorar_heatmap())

    t = threading.Thread(target=runner, daemon=True)
    t.start()
    return t


# Execução manual, se abrir só o heatmap_monitor.py
if __name__ == "__main__":
    start_heatmap_monitor()
    while True:
        time.sleep(1)
