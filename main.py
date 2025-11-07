import os, asyncio
from flask import Flask

app = Flask(__name__)

@app.route("/")
def home():
    return "OURO SENTINELA – Monitor ativo", 200

@app.route("/health")
def health():
    return "OK", 200

# loop placeholder (depois colocamos a lógica real dos alertas)
async def monitor_loop():
    while True:
        print("[OURO SENTINELA] varrendo o mercado...")
        await asyncio.sleep(30)

def run_async():
    loop = asyncio.get_event_loop()
    loop.create_task(monitor_loop())
    from threading import Thread
    Thread(target=lambda: app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))).start()
    loop.run_forever()

if __name__ == "__main__":
    run_async()
