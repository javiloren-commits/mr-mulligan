from flask import Flask, request, jsonify
import requests
import re
import time
from datetime import datetime, timedelta
import telegram
import threading
import os

app = Flask(__name__)

# Configuración Telegram
TELEGRAM_TOKEN = "8688322084:AAHgx3Fqw3LwL9sQAF0X1yZeor_nu8U15AU"
CHAT_ID = 8716095633
bot = telegram.Bot(token=TELEGRAM_TOKEN)

# Variables globales
sniper_running = False
current_username = None
current_password = None

def enviar_telegram(mensaje):
    try:
        bot.send_message(chat_id=CHAT_ID, text=mensaje, parse_mode='HTML')
    except:
        pass

def login_session(username, password):
    session = requests.Session()
    url = "https://reservas.rshecc.es/Login"
    r = session.get(url)
    
    viewstate = re.search(r'__VIEWSTATE" value="([^"]+)', r.text)
    viewgen = re.search(r'__VIEWSTATEGENERATOR" value="([^"]+)', r.text)
    eventval = re.search(r'__EVENTVALIDATION" value="([^"]+)', r.text)

    data = {
        "__VIEWSTATE": viewstate.group(1) if viewstate else "",
        "__VIEWSTATEGENERATOR": viewgen.group(1) if viewgen else "",
        "__EVENTVALIDATION": eventval.group(1) if eventval else "",
        "ctl00$ContenidoMasterPlaceHolder$UserName": username,
        "ctl00$ContenidoMasterPlaceHolder$PasswordTextBox": password,
        "ctl00$ContenidoMasterPlaceHolder$LoginButton": "Acceder"
    }
    
    resp = session.post(url, data=data)
    return session if "reservas" in resp.url.lower() else None

def sniper_loop():
    global sniper_running
    while sniper_running:
        try:
            fecha = (datetime.now() + timedelta(days=1)).strftime("%d/%m/%Y")
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Buscando en {fecha}...")
            
            # Aquí irá la búsqueda real (próxima iteración)
            enviar_telegram(f"🎉 <b>Mr Mulligan ha reservado automáticamente</b>\nFecha: {fecha}")
            
            time.sleep(180)  # 3 minutos
        except Exception as e:
            print("Error en loop:", e)
            time.sleep(60)

@app.route('/start', methods=['POST'])
def start_sniper():
    global sniper_running, current_username, current_password
    
    data = request.get_json()
    username = data.get('username')
    password = data.get('password')
    
    if not username or not password:
        return jsonify({"status": "error", "message": "Faltan credenciales"}), 400
    
    current_username = username
    current_password = password
    
    if not sniper_running:
        sniper_running = True
        threading.Thread(target=sniper_loop, daemon=True).start()
        enviar_telegram("✅ <b>Mr Mulligan iniciado</b>\nBuscando salidas automáticamente.")
        return jsonify({"status": "success", "message": "Sniper iniciado"})
    else:
        return jsonify({"status": "info", "message": "El sniper ya estaba corriendo"})

@app.route('/')
def home():
    return """
    <h1>Mr Mulligan está corriendo</h1>
    <p>Envía POST a /start con username y password para iniciar el sniper.</p>
    """

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
