from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
import re
import time
from datetime import datetime, timedelta
import telegram
import threading
import os

app = Flask(__name__)
CORS(app)  # ← Esto arregla el problema de conexión

# ====================== CONFIG ======================
TELEGRAM_TOKEN = "8688322084:AAHgx3Fqw3LwL9sQAF0X1yZeor_nu8U15AU"
CHAT_ID = 8716095633

# ====================== SESIÓN ======================
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

    # Aquí guardamos temporalmente (en memoria)
    print(f"✅ Sniper iniciado para usuario: {username}")
    
    # Iniciamos el bucle en segundo plano
    threading.Thread(target=sniper_loop, args=(username, password), daemon=True).start()
    
    return jsonify({"status": "success", "message": "Sniper iniciado correctamente"})

def sniper_loop(username, password):
    print(f"🚀 Bucle de búsqueda iniciado para {username}")
    while True:
        try:
            fecha = (datetime.now() + timedelta(days=1)).strftime("%d/%m/%Y")
            print(f"Buscando en {fecha}...")
            
            # Simulación por ahora
            bot.send_message(chat_id=CHAT_ID, text=f"🎉 <b>Mr Mulligan ha reservado</b>\nFecha: {fecha}", parse_mode='HTML')
            
            time.sleep(180)  # cada 3 minutos
        except Exception as e:
            print("Error:", e)
            time.sleep(60)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
