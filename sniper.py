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
    return "Mr Mulligan está corriendo ✅"

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
    print(f"🚀 Bucle iniciado para {username}")
    session = requests.Session()
    
    while True:
        try:
            fecha = (datetime.now() + timedelta(days=1)).strftime("%d/%m/%Y")
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Buscando en {fecha}...")

            # Login usando la API móvil
            login_url = f"https://reservas.rshecc.es/AppWebServices.7.0.0/Jugadores/json/login?centro=24&usuario={username}&clave={password}&procedencia=6&idioma=1"
            
            headers = {
                "Authorization": "Basic d2Vic2VydmljZTpTZWd1cmlkYWQqd2ViMTY=",
                "User-Agent": "StartMasterRSHECC/1 CFNetwork/3860.400.51 Darwin/25.3.0"
            }

            r = session.get(login_url, headers=headers)
            
            if r.status_code == 200 and "StatusOK" in r.text and "true" in r.text.lower():
                print("✅ Login exitoso")
                # Aquí irá la búsqueda real de huecos
                bot.send_message(chat_id=CHAT_ID, text=f"✅ Login OK\nBuscando en {fecha}...", parse_mode='HTML')
            else:
                print("❌ Login fallido")

            time.sleep(180)
        except Exception as e:
            print("Error:", e)
            time.sleep(60)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
