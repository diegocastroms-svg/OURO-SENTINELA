import os, asyncio, aiohttp, time
from datetime import datetime, timezone, timedelta
from flask import Flask
import threading

app = Flask(__name__)
@app.route("/")
def home():
    return "BOT RSI<25 + BOLLINGER DOWN — ONLINE", 200

@app.route("/health")
def health():
    return "OK", 200


# ======================
# CONFIGURAÇÕES
# ======================
BINANCE = "https://api.binance.com"
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = os.getenv("CHAT_ID", "")
VOLUME_MIN = 2_000_000
COOLDOWN = 600
PAIR_LIST = "https://api.binance.com/api/v3/ticker/price"

cooldown_dict = {}


def now_br():
    return datetime.now(timezone(timedelta(hours=-3))).strftime("%H:%M:%S BR")


# ======================
# TELEGRAM
# ======================
async def send(msg):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("⚠ SEM TOKEN OU CHAT_ID")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = {"chat_id": CHAT_ID, "text": msg}
    async with aiohttp.ClientSession() as s:
        await s.post(url, data=data)


# ======================
# MERCADO
# ======================
async def get_pairs():
    async with aiohttp.ClientSession() as s:
        async with s.get(PAIR_LIST) as r:
            data = await r.json()
            return [d["symbol"] for d in data if d["symbol"].endswith("USDT")]


async def get_klines(pair):
    url = f"{BINANCE}/api/v3/klines?symbol={pair}&interval=5m&limit=30"
    async with aiohttp.ClientSession() as s:
        async with s.get(url) as r:
            return await r.json()


def bollinger(values):
    import statistics as st
    mb = st.mean(values)
    sd = st.pstdev(values)
    return mb + 2 * sd, mb, mb - 2 * sd


def rsi(values, period=14):
    gains, losses = [], []

    for i in range(1, len(values)):
        diff = values[i] - values[i - 1]
        gains.append(max(0, diff))
        losses.append(abs(min(0, diff)))

    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period

    if avg_loss == 0:
        return 100

    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


# ======================
# MONITOR
# ======================
async def monitor():
    await asyncio.sleep(5)
    pairs = await get_pairs()

    print(">>> BOT INICIADO — MONITORANDO PARES USDT")

    while True:
        for pair in pairs:
            try:
                k = await get_klines(pair)
                if "code" in str(k):
                    continue

                closes = [float(c[4]) for c in k]
                volumes = [float(c[5]) for c in k]

                vol24 = sum(volumes)
                if vol24 < VOLUME_MIN:
                    continue

                # Bollinger
                up, mb, dn = bollinger(closes[-20:])
                up_prev, mb_prev, dn_prev = bollinger(closes[-21:-1])

                bandas_abrindo_baixo = (up < up_prev) and (dn < dn_prev)

                if not bandas_abrindo_baixo:
                    continue

                # Preço descendo
                descendo = closes[-1] < closes[-2] < closes[-3]
                if not descendo:
                    continue

                # RSI
                rsi_val = rsi(closes)
                if rsi_val >= 25:
                    continue

                # Cooldown
                last = cooldown_dict.get(pair, 0)
                if time.time() - last < COOLDOWN:
                    continue

                nome = pair.replace("USDT", "")
                preco = closes[-1]

                msg = (
f"⚠ FUNDO TÉCNICO\n"
f"{nome}\n\n"
f"Preço: {preco}\n"
f"RSI: {rsi_val:.2f}\n"
f"Bollinger abrindo para baixo + preço descendo\n"
f"⏰ {now_br()}"
                )

                await send(msg)
                cooldown_dict[pair] = time.time()

                print(f">>> ALERTA ENVIADO: {pair}")

            except Exception as e:
                print("ERRO:", e)

        await asyncio.sleep(2)


# ======================
# INICIAR LOOP
# ======================
def start_async():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(monitor())


threading.Thread(target=start_async, daemon=True).start()


# ======================
# INICIAR FLASK
# ======================
if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
