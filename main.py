import os, asyncio, aiohttp, time, threading
from datetime import datetime
from flask import Flask

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
PAIRS = os.getenv("PAIRS", "").split(",")

async def send_telegram(message):
    """Envia alerta para o Telegram"""
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("⚠️ Faltando TELEGRAM_TOKEN ou CHAT_ID")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    async with aiohttp.ClientSession() as session:
        await session.post(url, data={"chat_id": CHAT_ID, "text": message})

async def monitor_loop():
    """Loop principal de varredura de mercado"""
    print("🚀 OURO SENTINELA iniciado.")
    while True:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(BINANCE_API) as resp:
                    data = await resp.json()

            agora = datetime.now().strftime("%H:%M:%S")

            for coin in data:
                s = coin["symbol"]
                if PAIRS != [""] and s not in PAIRS:
                    continue
                price_change = float(coin["priceChangePercent"])
                volume = float(coin["quoteVolume"])

                # condição ajustada: detectar pumps mais cedo
                if price_change >= 3 and volume > 2_000_000:
                    msg = f"💥 {s} em alta de {price_change:.1f}%\nVolume: {volume/1_000_000:.1f}M\nHora: {agora}"
                    print(msg)
                    await send_telegram(msg)

            await asyncio.sleep(60)

        except Exception as e:
            print("⚠️ Erro no loop:", e)
            await asyncio.sleep(10)


if __name__ == "__main__":
    # inicia o Flask e o monitor em paralelo
    threading.Thread(target=lambda: app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))).start()
    time.sleep(2)
    asyncio.run(monitor_loop())
