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
CORS(app)

# ====================== VARIABLES DE ENTORNO ======================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

if not TELEGRAM_TOKEN or not CHAT_ID:
    print("❌ ERROR: TELEGRAM_TOKEN o CHAT_ID no configurados en variables de entorno")

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

    print(f"✅ Sniper iniciado para usuario: {username}")
    
    # Inicia el bucle en segundo plano con las credenciales recibidas
    threading.Thread(target=sniper_loop, args=(username, password), daemon=True).start()
    
    return jsonify({"status": "success", "message": "Sniper iniciado correctamente"})

def sniper_loop(username, password):
    print(f"🚀 Bucle de búsqueda iniciado para {username}")
    while True:
        try:
            fecha = (datetime.now() + timedelta(days=1)).strftime("%d/%m/%Y")
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Buscando en {fecha}...")
            
            # Simulación (luego pondremos la búsqueda y reserva real)
            bot.send_message(
                chat_id=CHAT_ID, 
                text=f"🎉 <b>Mr Mulligan ha reservado automáticamente</b>\nFecha: {fecha}", 
                parse_mode='HTML'
            )
            
            time.sleep(180)  # cada 3 minutos
        except Exception as e:
            print("Error en loop:", e)
            time.sleep(60)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)    # Iniciamos el bucle en segundo plano
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
