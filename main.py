import os, asyncio, aiohttp, time
from datetime import datetime
from flask import Flask
import threading

app = Flask(__name__)

@app.route("/")
def home():
    return "OURO SENTINELA – Monitor ativo", 200

@app.route("/health")
def health():
    return "OK", 200


BINANCE_API = "https://api.binance.com/api/v3/ticker/24hr"
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = os.getenv("CHAT_ID", "")
PAIRS = os.getenv("PAIRS", "BTCUSDT,ETHUSDT").split(",")

async def send_telegram(message):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("⚠️ Faltando TELEGRAM_TOKEN ou CHAT_ID")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    async with aiohttp.ClientSession() as session:
        await session.post(url, data={"chat_id": CHAT_ID, "text": message})

async def monitor_loop():
    print("🚀 OURO SENTINELA iniciado.")
    while True:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(BINANCE_API) as resp:
                    data = await resp.json()

            agora = datetime.now().strftime("%H:%M:%S")
            for coin in data:
                s = coin["symbol"]
                if s not in PAIRS:
                    continue
                price_change = float(coin["priceChangePercent"])
                volume = float(coin["quoteVolume"])
                if price_change >= 5 and volume > 10000000:
                    msg = f"💥 {s} em alta de {price_change:.1f}%\nVolume: {volume/1_000_000:.1f}M\nHora: {agora}"
                    print(msg)
                    await send_telegram(msg)
            await asyncio.sleep(60)
        except Exception as e:
            print("Erro no loop:", e)
            await asyncio.sleep(10)

def run_async():
    loop = asyncio.get_event_loop()
    loop.create_task(monitor_loop())
    threading.Thread(target=lambda: app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))).start()
    loop.run_forever()

if __name__ == "__main__":
    run_async()
