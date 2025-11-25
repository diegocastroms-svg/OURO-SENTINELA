# heatmap_monitor.py — Monitor simples para detectar FUNDO REAL
import os, asyncio, aiohttp, time, threading
from datetime import datetime

BINANCE = "https://api.binance.com"

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()


# =====================================================
# ATIVAR / DESATIVAR ALERTAS
# =====================================================
ALERTA_FUNDO = True   # alerta de fundo ATIVO
ALERTA_TOPO  = False  # alerta de topo DESLIGADO


# =====================================================
# FILTRO DE MOEDAS SPOT USDT (SEM FIAT / STABLE FRACA)
# =====================================================
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
        data = await (await s.get(f"{BINANCE}/api/v3/exchangeInfo")).json()
        pares = []

        for sym in data["symbols"]:
            if sym["status"] != "TRADING": continue
            if sym["quoteAsset"] != "USDT": continue

            base = sym["baseAsset"]
            if base in BLOQUEIO_BASE: continue
            if any(p in base for p in PADROES_LIXO): continue

            pares.append(sym["symbol"])

        return pares


PAIRS = []
INTERVALO = 60
COOLDOWN = 900


# PARA NÃO REPETIR ALERTA
last_alert = {}
ultima_direcao = {}


def br_time():
    return datetime.now().strftime("%H:%M:%S")


async def tg(session, msg):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print(f"[{br_time()}] (TG OFF) {msg}")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    await session.post(url, json={"chat_id": CHAT_ID, "text": msg})


# =====================================================
# BUSCAR OHLC SIMPLES (KLINES)
# =====================================================
async def get_ohlc(session, sym):
    url = f"{BINANCE}/api/v3/klines"
    params = {"symbol": sym, "interval": "5m", "limit": 5}

    try:
        js = await (await session.get(url, params=params)).json()
        return js
    except:
        return None


# =====================================================
# LÓGICA DO ALERTA DE FUNDO (SIMPLIFICADO)
# =====================================================
def detectar_fundo(klines):
    # Pegando candles
    c1 = klines[-1]  # atual
    c2 = klines[-2]
    c3 = klines[-3]
    c4 = klines[-4]

    # convertendo
    def conv(c): 
        return float(c[1]), float(c[2]), float(c[3]), float(c[4])

    o1,h1,l1,cl1 = conv(c1)
    o2,h2,l2,cl2 = conv(c2)
    o3,h3,l3,cl3 = conv(c3)
    o4,h4,l4,cl4 = conv(c4)

    # queda forte
    queda_forte = (cl2 < o2) and (o2 - cl2 > (h2 - l2) * 0.50)

    # lateralizou
    lateral = (
        abs(cl3 - cl2) < (h3 - l3)*0.25 and
        abs(cl4 - cl3) < (h4 - l4)*0.25
    )

    # primeiro candle de reversão
    reversao = cl1 > o1 and l1 > l2

    if queda_forte and lateral and reversao:
        return True
    
    return False



def pode_alertar(sym):
    t = time.time()
    if sym not in last_alert:
        last_alert[sym] = t
        return True

    if t - last_alert[sym] < COOLDOWN:
        return False

    last_alert[sym] = t
    return True


# =====================================================
# MONITOR PRINCIPAL
# =====================================================
async def monitor():
    global PAIRS

    if not PAIRS:
        PAIRS = await carregar_pairs_validos()
        print(f"[{br_time()}] Monitorando: {PAIRS}")

    async with aiohttp.ClientSession() as s:
        while True:
            for sym in PAIRS:
                try:
                    kl = await get_ohlc(s, sym)
                    if not kl: continue

                    # DETECTAR FUNDO
                    if ALERTA_FUNDO and detectar_fundo(kl):

                        if not pode_alertar(sym):
                            continue

                        preco = float(kl[-1][4])
                        fundo = float(kl[-2][3])
                        base = sym.replace("USDT", "")

                        msg = (
                            f"🔔 FUNDO DETECTADO\n\n"
                            f"{base}\n\n"
                            f"Preço atual: {preco:.6f}\n"
                            f"Fundo recente: {fundo:.6f}\n"
                            f"Primeiro candle de reversão confirmado\n"
                            f"Possível início de movimento de alta."
                        )
                        await tg(s, msg)

                except Exception as e:
                    print(f"[{br_time()}] ERRO {sym}: {e}")

            await asyncio.sleep(INTERVALO)



def start():
    threading.Thread(target=lambda: asyncio.run(monitor()), daemon=True).start()


if __name__ == "__main__":
    asyncio.run(monitor())
