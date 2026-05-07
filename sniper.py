from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
import time
from datetime import datetime, timedelta
import telegram
import threading
import os

app = Flask(__name__)
CORS(app)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

bot = telegram.Bot(token=TELEGRAM_TOKEN)

@app.route('/')
def home():
    return "Mr Mulligan está corriendo correctamente ✅"

@app.route('/start', methods=['POST'])
def start_sniper():
    data = request.get_json()
    username = data.get('username')
    password = data.get('password')

    if not username or not password:
        return jsonify({"status": "error", "message": "Faltan credenciales"}), 400

    print(f"✅ Sniper iniciado para {username}")
    threading.Thread(target=sniper_loop, args=(username, password), daemon=True).start()
    
    return jsonify({"status": "success", "message": "Sniper iniciado correctamente"})

def sniper_loop(username, password):
    print(f"🚀 Bucle automático iniciado para {username}")
    session = requests.Session()
    
    headers = {
        "Authorization": "Basic d2Vic2VydmljZTpTZWd1cmlkYWQqd2ViMTY=",
        "User-Agent": "StartMasterRSHECC/1 CFNetwork/3860.400.51 Darwin/25.3.0"
    }

    while True:
        try:
            fecha_str = (datetime.now() + timedelta(days=1)).strftime("%d/%m/%Y")
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Buscando hueco en {fecha_str}...")

            # Aquí irá la búsqueda real + reserva automática del primer hueco
            # Por ahora simulamos éxito para probar el flujo completo
            hora_encontrada = "08:12"

            bot.send_message(
                chat_id=CHAT_ID,
                text=f"🎉 <b>RESERVA REALIZADA AUTOMÁTICAMENTE</b>\n\n"
                     f"Fecha: <b>{fecha_str}</b>\n"
                     f"Hora: <b>{hora_encontrada}</b> (primer hueco disponible)\n"
                     f"Tipo: Norte — 18 Hoyos\n\n"
                     f"Mr Mulligan ha reservado por ti ✅",
                parse_mode='HTML'
            )

            # Llamada al frontend para mostrar en UI
            # (esto lo mejoraremos después con WebSocket o polling)

            time.sleep(180)  # cada 3 minutos

        except Exception as e:
            print("Error:", e)
            time.sleep(60)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
