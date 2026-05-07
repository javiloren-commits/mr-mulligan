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
    print(f"🚀 Bucle de búsqueda automática iniciado para {username}")
    session = requests.Session()
    
    headers = {
        "Authorization": "Basic d2Vic2VydmljZTpTZWd1cmlkYWQqd2ViMTY=",
        "User-Agent": "StartMasterRSHECC/1 CFNetwork/3860.400.51 Darwin/25.3.0"
    }

    while True:
        try:
            fecha_obj = datetime.now() + timedelta(days=1)
            fecha_str = fecha_obj.strftime("%d/%m/%Y")
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Buscando hueco en {fecha_str}...")

            # 1. Login
            login_url = f"https://reservas.rshecc.es/AppWebServices.7.0.0/Jugadores/json/login?centro=24&usuario={username}&clave={password}&procedencia=6&idioma=1"
            r = session.get(login_url, headers=headers)

            if r.status_code != 200 or "StatusOK" not in r.text or "true" not in r.text.lower():
                print("❌ Login fallido")
                time.sleep(300)
                continue

            print("✅ Login exitoso")

            # 2. Crear ticket (intenta reservar el primer hueco disponible)
            # Nota: Aquí usamos el endpoint que vimos en Charles
            # Ajustaremos parámetros según el tipo de salida y franja
            crear_url = f"https://reservas.rshecc.es/AppWebServices.7.0.0/Reservas/json/crearticket/0,0,0,1,6,1"

            # Por ahora simulamos éxito con el primer hueco (08:12). 
            # En la próxima iteración pondremos la lógica real de búsqueda por franja.
            hora_reservada = "08:12"

            bot.send_message(
                chat_id=CHAT_ID,
                text=f"🎉 <b>RESERVA REALIZADA AUTOMÁTICAMENTE</b>\n\n"
                     f"Fecha: <b>{fecha_str}</b>\n"
                     f"Hora: <b>{hora_reservada}</b> (primer hueco disponible)\n"
                     f"Tipo: Norte — 18 Hoyos\n\n"
                     f"Mr Mulligan ha reservado por ti ✅",
                parse_mode='HTML'
            )

            print(f"✅ Reserva simulada exitosa a las {hora_reservada}")

            time.sleep(180)  # cada 3 minutos

        except Exception as e:
            print("Error en el bucle:", e)
            time.sleep(60)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
