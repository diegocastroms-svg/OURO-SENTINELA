# heatmap_monitor.py — versão automática para TODAS as moedas do SPOT USDT
import os, asyncio, aiohttp, time
from datetime import datetime

BINANCE = "https://api.binance.com"

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()

# Intervalo do heatmap (30s conforme pedido)
HEATMAP_INTERVAL = 30

# Filtros globais
MIN_VOL24 = 5_000_000   # mínimo para não ser moeda morta
MAX_DIST_PCT = 0.10     # 10% do preço para buscar cluster
MIN_CLUSTER_USD = 150_000
DOM_RATIO = 1.3         # cluster precisa ser 30% maior que o outro
ALERT_COOLDOWN = 900    # 15 minutos por direção

_last_alert = {}

STABLES_FIAT = {
    "USDC", "FDUSD", "BUSD", "TUSD", "USDE",
    "EUR", "GBP", "TRY", "BRL", "AUD", "CAD", "RUB"
}

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
    except:
        pass

async def get_json(session, url, params=None, timeout=10):
    try:
        async with session.get(url, params=params, timeout=timeout) as r:
            return await r.json()
    except:
        return None

async def fetch_24hr(session):
    return await get_json(session, f"{BINANCE}/api/v3/ticker/24hr")

async def fetch_depth(session, sym, limit=100):
    return await get_json(
        session,
        f"{BINANCE}/api/v3/depth",
        {"symbol": sym, "limit": limit}
    )

def analisar_book(depth, mid_price):
    asks = [(float(p), float(q)) for p, q in depth.get("asks", [])]
    bids = [(float(p), float(q)) for p, q in depth.get("bids", [])]

    max_up, max_down = None, None
    max_dist = mid_price * MAX_DIST_PCT

    for p, q in asks:
        if p <= mid_price: continue
        if p - mid_price > max_dist: continue
        notional = p * q
        if notional < MIN_CLUSTER_USD: continue
        if not max_up or notional > max_up["notional"]:
            max_up = {"price": p, "notional": notional}

    for p, q in bids:
        if p >= mid_price: continue
        if mid_price - p > max_dist: continue
        notional = p * q
        if notional < MIN_CLUSTER_USD: continue
        if not max_down or notional > max_down["notional"]:
            max_down = {"price": p, "notional": notional}

    return {"cluster_up": max_up, "cluster_down": max_down}

def decidir_direcao(info):
    up = info["cluster_up"]
    down = info["cluster_down"]

    if not up and not down:
        return {"side": "FLAT", "dom": 0.0}

    if up and not down:
        return {"side": "UP", "dom": 1.0}

    if down and not up:
        return {"side": "DOWN", "dom": 1.0}

    if up["notional"] > down["notional"] * DOM_RATIO:
        dom = up["notional"] / (up["notional"] + down["notional"])
        return {"side": "UP", "dom": dom}

    if down["notional"] > up["notional"] * DOM_RATIO:
        dom = down["notional"] / (up["notional"] + down["notional"])
        return {"side": "DOWN", "dom": dom}

    return {"side": "FLAT", "dom": 0.0}

def pode_alertar(symbol, side):
    now = time.time()
    info = _last_alert.get(symbol)

    if not info:
        _last_alert[symbol] = {"ts": now, "side": side}
        return True

    if info["side"] != side:
        _last_alert[symbol] = {"ts": now, "side": side}
        return True

    if now - info["ts"] < ALERT_COOLDOWN:
        return False

    _last_alert[symbol] = {"ts": now, "side": side}
    return True

async def monitorar_heatmap():
    async with aiohttp.ClientSession() as session:
        print(f"[{br_time()}] [HEATMAP] Iniciando varredura automática SPOT USDT...")

        while True:
            all24 = await fetch_24hr(session)
            if not all24:
                print(f"[{br_time()}] [HEATMAP] Erro ao obter 24hr.")
                await asyncio.sleep(HEATMAP_INTERVAL)
                continue

            # Seleciona todos os pares SPOT/USDT vivos
            pares = [
                x["symbol"] for x in all24
                if x["symbol"].endswith("USDT")
                and x["symbol"].replace("USDT", "") not in STABLES_FIAT
                and float(x["quoteVolume"]) >= MIN_VOL24
            ]

            for symbol in pares:
                try:
                    depth = await fetch_depth(session, symbol)
                    if not depth: continue

                    best_bid = float(depth["bids"][0][0])
                    best_ask = float(depth["asks"][0][0])
                    mid = (best_bid + best_ask) / 2

                    info = analisar_book(depth, mid)
                    decision = decidir_direcao(info)

                    side, dom = decision["side"], decision["dom"]

                    if side in ("UP", "DOWN") and dom >= 0.55:
                        cluster = info["cluster_up"] if side == "UP" else info["cluster_down"]
                        if cluster and pode_alertar(symbol, side):
                            await tg(session,
                                f"{br_time()} — HEATMAP {symbol}\n"
                                f"Direção: {'ALTA' if side=='UP' else 'QUEDA'}\n"
                                f"Cluster alvo: {cluster['price']:.6f}\n"
                                f"Notional: ~{cluster['notional']:,.0f} USDT\n"
                                f"Dominância: {dom*100:.1f}%"
                            )

                except Exception as e:
                    print(f"[{br_time()}] [HEATMAP-ERROR] {symbol}: {e}")

            await asyncio.sleep(HEATMAP_INTERVAL)

if __name__ == "__main__":
    asyncio.run(monitorar_heatmap())
