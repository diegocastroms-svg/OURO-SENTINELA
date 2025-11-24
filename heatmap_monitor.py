# heatmap_monitor.py — Monitor simples de “heatmap” usando apenas o book da Binance (GRÁTIS)
# Pensado pra encaixar no padrão do OURO-TENDÊNCIA v1.2, sem mexer no código principal.

import os, asyncio, aiohttp, time
from datetime import datetime

BINANCE = "https://api.binance.com"

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()

# Lista de pares a monitorar (ex.: "OGUSDT,TSTUSDT,GPSUSDT")
HEATMAP_PAIRS = os.getenv("HEATMAP_PAIRS", "").strip()
PAIRS = [p.strip() for p in HEATMAP_PAIRS.split(",") if p.strip()]

# Intervalo entre varreduras do “heatmap” (segundos)
HEATMAP_INTERVAL = int(os.getenv("HEATMAP_INTERVAL", "60"))

# Distância máxima (em %) a partir do preço médio pra procurar clusters
HEATMAP_MAX_DIST_PCT = float(os.getenv("HEATMAP_MAX_DIST_PCT", "0.10"))  # 10%

# Valor mínimo em USDT pra considerar um “cluster forte”
HEATMAP_MIN_CLUSTER_USD = float(os.getenv("HEATMAP_MIN_CLUSTER_USD", "150000"))

# Diferença mínima entre cluster up/down pra dizer que há direção (1.3 = 30% maior)
HEATMAP_MIN_DOMINANCE_RATIO = float(os.getenv("HEATMAP_MIN_DOMINANCE_RATIO", "1.3"))

# Cooldown de alerta por par/direção (segundos)
HEATMAP_ALERT_COOLDOWN = int(os.getenv("HEATMAP_ALERT_COOLDOWN", "900"))  # 15 min

_last_alert = {}  # { "OGUSDT": {"ts": 1234567890, "side": "UP" / "DOWN"} }


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
    except Exception as e:
        print(f"[{br_time()}] [TG-ERROR] {e}")


async def get_json(session, url, params=None, timeout=15):
    for _ in range(2):
        try:
            async with session.get(url, params=params, timeout=timeout) as r:
                return await r.json()
        except Exception:
            await asyncio.sleep(0.4)
    return None


async def fetch_depth(session, sym, limit=100):
    return await get_json(
        session,
        f"{BINANCE}/api/v3/depth",
        {"symbol": sym, "limit": limit},
    )


def analisar_book(depth: dict, mid_price: float) -> dict:
    """
    Lê o book e encontra:
      - maior cluster de venda acima (asks)
      - maior cluster de compra abaixo (bids)
    dentro de HEATMAP_MAX_DIST_PCT a partir do preço médio (mid_price).

    Retorna:
      {
        "cluster_up": {"price": ..., "notional": ...} ou None,
        "cluster_down": {"price": ..., "notional": ...} ou None
      }
    """
    asks = [(float(p), float(q)) for p, q in depth.get("asks", [])]
    bids = [(float(p), float(q)) for p, q in depth.get("bids", [])]

    max_up = None
    max_down = None

    max_dist = mid_price * HEATMAP_MAX_DIST_PCT

    # Cluster de venda acima (asks)
    for p, q in asks:
        if p <= mid_price:
            continue
        if p - mid_price > max_dist:
            continue
        notional = p * q
        if notional < HEATMAP_MIN_CLUSTER_USD:
            continue
        if (max_up is None) or (notional > max_up["notional"]):
            max_up = {"price": p, "notional": notional}

    # Cluster de compra abaixo (bids)
    for p, q in bids:
        if p >= mid_price:
            continue
        if mid_price - p > max_dist:
            continue
        notional = p * q
        if notional < HEATMAP_MIN_CLUSTER_USD:
            continue
        if (max_down is None) or (notional > max_down["notional"]):
            max_down = {"price": p, "notional": notional}

    return {
        "cluster_up": max_up,
        "cluster_down": max_down,
    }


def decidir_direcao(info: dict) -> dict:
    """
    Recebe info com cluster_up/cluster_down e decide se:
      - há dominância para cima
      - há dominância para baixo
      - ou mercado indeciso

    Retorna:
      {
        "side": "UP" / "DOWN" / "FLAT",
        "dominance": float (0-1),
      }
    """
    up = info.get("cluster_up")
    down = info.get("cluster_down")

    if not up and not down:
        return {"side": "FLAT", "dominance": 0.0}

    if up and not down:
        return {"side": "UP", "dominance": 1.0}

    if down and not up:
        return {"side": "DOWN", "dominance": 1.0}

    up_n = up["notional"]
    down_n = down["notional"]

    if up_n > down_n * HEATMAP_MIN_DOMINANCE_RATIO:
        dom = up_n / (up_n + down_n)
        return {"side": "UP", "dominance": dom}

    if down_n > up_n * HEATMAP_MIN_DOMINANCE_RATIO:
        dom = down_n / (up_n + down_n)
        return {"side": "DOWN", "dominance": dom}

    return {"side": "FLAT", "dominance": 0.0}


def _pode_alertar(symbol: str, side: str) -> bool:
    """
    Cooldown simples por par/direção.
    """
    now_ts = time.time()
    info = _last_alert.get(symbol)

    if not info:
        _last_alert[symbol] = {"ts": now_ts, "side": side}
        return True

    # mudou o lado (UP -> DOWN ou DOWN -> UP) → libera alerta
    if info["side"] != side:
        _last_alert[symbol] = {"ts": now_ts, "side": side}
        return True

    # ainda dentro do cooldown → não alerta
    if now_ts - info["ts"] < HEATMAP_ALERT_COOLDOWN:
        return False

    _last_alert[symbol] = {"ts": now_ts, "side": side}
    return True


async def monitorar_heatmap():
    """
    Loop principal do monitor de "heatmap" baseado no book.
    Pode ser rodado em paralelo ao bot principal.
    Exemplo de uso no seu código principal:
        asyncio.create_task(monitorar_heatmap())
    """
    if not PAIRS:
        print(f"[{br_time()}] [HEATMAP] Nenhum par configurado em HEATMAP_PAIRS.")
        return

    print(f"[{br_time()}] [HEATMAP] Monitor de heatmap iniciado para: {', '.join(PAIRS)}")

    async with aiohttp.ClientSession() as session:
        while True:
            for symbol in PAIRS:
                try:
                    depth = await fetch_depth(session, symbol, limit=100)
                    if not depth or not depth.get("bids") or not depth.get("asks"):
                        continue

                    best_bid = float(depth["bids"][0][0])
                    best_ask = float(depth["asks"][0][0])
                    mid = (best_bid + best_ask) / 2.0

                    info = analisar_book(depth, mid)
                    decision = decidir_direcao(info)

                    side = decision["side"]
                    dom = decision["dominance"]

                    if side in ("UP", "DOWN") and dom >= 0.55:
                        cluster = info["cluster_up"] if side == "UP" else info["cluster_down"]
                        if cluster and _pode_alertar(symbol, side):
                            alvo = cluster["price"]
                            notional = cluster["notional"]
                            msg = (
                                f"{br_time()} — HEATMAP {symbol}\n"
                                f"Preço médio atual: {mid:.6f}\n"
                                f"Direção provável: {'ALTA' if side == 'UP' else 'QUEDA'}\n"
                                f"Cluster {'acima' if side == 'UP' else 'abaixo'}: {alvo:.6f}\n"
                                f"Notional estimado do cluster: ~{notional:,.0f} USDT\n"
                                f"Dominância desse lado: {dom*100:.1f}%\n"
                            )
                            await tg(session, msg)

                except Exception as e:
                    print(f"[{br_time()}] [HEATMAP-ERROR] {symbol} {e}")

            await asyncio.sleep(HEATMAP_INTERVAL)


if __name__ == "__main__":
    # Execução independente opcional (se rodar este arquivo sozinho)
    asyncio.run(monitorar_heatmap())
