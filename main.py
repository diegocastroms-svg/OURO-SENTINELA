import os, asyncio, aiohttp, time, threading, statistics, csv
from datetime import datetime
from flask import Flask, send_file, Response

from heatmap_monitor import start_heatmap_monitor  # <<<<<< ADICIONADO

# =========================
# CONFIGURAÇÃO (versão equilibrada)
# =========================
BINANCE = "https://api.binance.com"
TOP_N = int(os.getenv("TOP_N", "30"))
SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL", "180"))
MIN_QV_USDT = float(os.getenv("MIN_QV_USDT", "15000000"))
BOOK_MIN_BUY = float(os.getenv("BOOK_MIN_BUY", "1.55"))
BOOK_MIN_SELL = float(os.getenv("BOOK_MIN_SELL", "0.65"))

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()
PAIRS = os.getenv("PAIRS", "").strip()

CSV_FILE = "dados_coletados.csv"

# =========================
# APP WEB (Render health)
# =========================
app = Flask(__name__)

historico = []


@app.route("/")
def home():
    return "OURO-TENDÊNCIA v1.2 – Coleta de Padrões ATIVA", 200


@app.route("/health")
def health():
    return "OK", 200


@app.route("/resultado")
def resultado():
    linhas = historico[-500:]
    linhas = list(reversed(linhas))

    html = [
        "<html><head><meta charset='utf-8'><title>Resultado OURO-TENDÊNCIA</title>",
        "<style>",
        "body { font-family: Arial, sans-serif; background:#111; color:#eee; }",
        "table { border-collapse: collapse; width: 100%; font-size: 12px; }",
        "th, td { border: 1px solid #444; padding: 4px 6px; text-align: center; }",
        "th { background: #222; }",
        ".forte-alta { background:#063; }",
        ".forte-baixa { background:#600; }",
        "</style></head><body>",
        "<h2>OURO-TENDÊNCIA v1.2 – Resultado do Monitoramento</h2>",
        f"<p>Total de registros: {len(historico)}</p>",
        "<p><a href='/download' style='color:#0af'>⬇️ Baixar CSV completo</a></p>",
        "<table>",
        "<tr>",
        "<th>Hora</th><th>Moeda</th><th>Direção</th><th>Força</th>",
        "<th>RSI</th><th>Book</th><th>Vol x</th>",
        "</tr>"
    ]

    for row in linhas:
        cls = ""
        if row["direcao"] == "alta" and row["forca"] >= 90 and row["book"] >= BOOK_MIN_BUY:
            cls = "forte-alta"
        elif row["direcao"] == "baixa" and row["forca"] >= 90 and row["book"] <= BOOK_MIN_SELL:
            cls = "forte-baixa"

        html.append(
            f"<tr class='{cls}'>"
            f"<td>{row['hora']}</td>"
            f"<td>{row['moeda']}</td>"
            f"<td>{row['direcao']}</td>"
            f"<td>{row['forca']}</td>"
            f"<td>{row['rsi']:.1f}</td>"
            f"<td>{row['book']:.2f}</td>"
            f"<td>{row['vol_mult']:.1f}</td>"
            f"</tr>"
        )

    html.append("</table></body></html>")
    return Response("".join(html), mimetype="text/html")


@app.route("/download")
def download():
    if not os.path.exists(CSV_FILE):
        return "Arquivo ainda não gerado. Deixe o bot rodando alguns minutos.", 404
    return send_file(CSV_FILE, as_attachment=True)


# =========================
# UTILITÁRIOS
# =========================
def br_time():
    return datetime.now().strftime("%H:%M:%S")


async def get_json(session, url, params=None, timeout=15):
    for _ in range(2):
        try:
            async with session.get(url, params=params, timeout=timeout) as r:
                return await r.json()
        except Exception:
            await asyncio.sleep(0.4)
    return None


async def fetch_24hr(session):
    return await get_json(session, f"{BINANCE}/api/v3/ticker/24hr")


async def fetch_klines(session, sym, interval="5m", limit=120):
    return await get_json(
        session,
        f"{BINANCE}/api/v3/klines",
        {"symbol": sym, "interval": interval, "limit": limit},
    )


async def fetch_depth(session, sym, limit=40):
    return await get_json(
        session,
        f"{BINANCE}/api/v3/depth",
        {"symbol": sym, "limit": limit},
    )


# =========================
# INDICADORES
# =========================
def ema(values, period):
    if not values:
        return 0.0
    k = 2 / (period + 1)
    e = values[0]
    for v in values[1:]:
        e = v * k + e * (1 - k)
    return e


def macd(values, fast=12, slow=26, signal=9):
    if len(values) < slow + signal:
        return 0.0, 0.0
    window = values[-(slow + signal):]
    ema_fast = ema(window, fast)
    ema_slow = ema(window, slow)
    macd_line = ema_fast - ema_slow
    sig = ema([macd_line] * signal, signal)
    return macd_line, macd_line - sig


def rsi(values, period=14):
    if len(values) < period + 1:
        return 50.0
    gains = [max(values[i] - values[i - 1], 0.0) for i in range(1, len(values))]
    losses = [max(values[i - 1] - values[i], 0.0) for i in range(1, len(values))]
    ag = sum(gains[-period:]) / period
    al = sum(losses[-period:]) / period or 1e-9
    return 100.0 - (100.0 / (1.0 + ag / al))


def book_ratio(depth):
    def power(levels):
        return sum(float(p) * float(q) for p, q in levels[:20])
    b = power(depth.get("bids", []))
    a = power(depth.get("asks", []))
    return b / a if a > 0 else 0.0


def decide_direction(ema9, ema20, macd_line):
    if ema9 > ema20 and macd_line > 0:
        return "alta"
    if ema9 < ema20 and macd_line < 0:
        return "baixa"
    return "neutra"


def calc_force(ema9, ema20, macd_line, hist, rsi_val, ratio):
    score = 0
    if ema9 > ema20:
        score += 15
    if macd_line > 0 and hist > 0:
        score += 25
    if 45 < rsi_val < 65:
        score += 10
    if ratio >= BOOK_MIN_BUY:
        score += 50
    if ema9 < ema20 and macd_line < 0 and ratio <= BOOK_MIN_SELL:
        score = max(score, 80)
    return min(score, 100)


# =========================
# LOOP PRINCIPAL (COLETA)
# =========================
async def scan_once():
    print(f"[{br_time()}] === Iniciando varredura ===")
    async with aiohttp.ClientSession() as s:
        all24 = await fetch_24hr(s)
        if not all24:
            print(f"[{br_time()}] Erro ao obter 24hr.")
            return

        # SE vier dict de erro, não tenta iterar
        if isinstance(all24, dict):
            print(f"[{br_time()}] Resposta inesperada do 24hr: {all24}")
            return

        allow = set(PAIRS.split(",")) if PAIRS else None

        pool = []
        for x in all24:
            # garante que x é dict com as chaves necessárias
            if not isinstance(x, dict):
                continue
            sym = x.get("symbol")
            qv = x.get("quoteVolume")

            if not sym or not isinstance(sym, str):
                continue
            if not sym.endswith("USDT"):
                continue
            if allow and sym not in allow:
                continue
            try:
                qv_f = float(qv)
            except (TypeError, ValueError):
                continue
            if qv_f < MIN_QV_USDT:
                continue

            pool.append((sym, qv_f))

        symbols = [sym for sym, _ in sorted(pool, key=lambda t: t[1], reverse=True)[:TOP_N]]

        print(f"[{br_time()}] Monitorando {len(symbols)} pares: {', '.join(symbols)}")

        if not os.path.exists(CSV_FILE):
            with open(CSV_FILE, "w", newline="") as f:
                w = csv.writer(f)
                w.writerow([
                    "hora", "moeda", "direcao", "forca",
                    "rsi", "book", "vol_mult"
                ])

        for sym in symbols:
            try:
                kl, dp = await asyncio.gather(
                    fetch_klines(s, sym, "5m", 120),
                    fetch_depth(s, sym, 40),
                )
                if not kl or not dp:
                    continue

                closes = [float(k[4]) for k in kl]
                vols = [float(k[5]) for k in kl]

                ema9 = ema(closes[-40:], 9)
                ema20 = ema(closes[-40:], 20)
                macd_line, hist = macd(closes)
                rsi_val = rsi(closes)

                base = statistics.median(vols[-11:-1]) or 1.0
                vol_mult = vols[-1] / base
                ratio = book_ratio(dp)

                direcao = decide_direction(ema9, ema20, macd_line)
                forca = calc_force(ema9, ema20, macd_line, hist, rsi_val, ratio)

                registro = {
                    "hora": br_time(),
                    "moeda": sym,
                    "direcao": direcao,
                    "forca": forca,
                    "rsi": rsi_val,
                    "book": ratio,
                    "vol_mult": vol_mult
                }
                historico.append(registro)
                if len(historico) > 5000:
                    del historico[:len(historico) - 5000]

                with open(CSV_FILE, "a", newline="") as f:
                    w = csv.writer(f)
                    w.writerow([
                        registro["hora"],
                        registro["moeda"],
                        registro["direcao"],
                        registro["forca"],
                        f"{registro['rsi']:.2f}",
                        f"{registro['book']:.4f}",
                        f"{registro['vol_mult']:.2f}",
                    ])

                print(
                    f"[{br_time()}] COLETADO: {sym} | dir={direcao} | força={forca}% | "
                    f"RSI={rsi_val:.1f} | book={ratio:.2f} | vol x{vol_mult:.1f}"
                )

                await asyncio.sleep(0.05)

            except Exception as e:
                print(f"[{br_time()}] Erro analisando {sym}: {e}")

    print(f"[{br_time()}] === Varredura finalizada ===")


async def monitor_loop():
    print("🚀 OURO-TENDÊNCIA v1.2 – COLETA DE PADRÕES ATIVA.")
    while True:
        try:
            await scan_once()
            await asyncio.sleep(SCAN_INTERVAL)
        except Exception as e:
            print(f"[{br_time()}] ⚠️ Erro no loop principal: {e}")
            await asyncio.sleep(5)


def start_background_loop():
    def runner():
        asyncio.run(monitor_loop())
    t = threading.Thread(target=runner, daemon=True)
    t.start()
    return t


# ======== ATIVAÇÃO DOS DOIS MOTORES =========
start_background_loop()
start_heatmap_monitor()   # heatmap em paralelo

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
