import requests
import re
import time
from datetime import datetime, timedelta
import telegram

# ====================== CONFIGURACIÓN ======================
USERNAME = "0000006950"
PASSWORD = "200437"

TELEGRAM_TOKEN = "8688322084:AAHgx3Fqw3LwL9sQAF0X1yZeor_nu8U15AU"
CHAT_ID = 8716095633

# Preferencias de búsqueda
INTERVALO_SEGUNDOS = 180        # 3 minutos
DIAS_A_BUSCAR = 14

# ====================== SESIÓN ======================
session = requests.Session()
bot = telegram.Bot(token=TELEGRAM_TOKEN)

def enviar_telegram(mensaje):
    try:
        bot.send_message(chat_id=CHAT_ID, text=mensaje, parse_mode='HTML')
        print(f"📨 Telegram enviado: {mensaje[:80]}...")
    except Exception as e:
        print("Error enviando Telegram:", e)

def login():
    print("🔑 Intentando login...")
    url = "https://reservas.rshecc.es/Login"
    r = session.get(url)
    
    viewstate = re.search(r'__VIEWSTATE" value="([^"]+)', r.text)
    viewgen = re.search(r'__VIEWSTATEGENERATOR" value="([^"]+)', r.text)
    eventval = re.search(r'__EVENTVALIDATION" value="([^"]+)', r.text)

    if not viewstate or not viewgen:
        print("❌ No se pudieron obtener tokens")
        return False

    data = {
        "__VIEWSTATE": viewstate.group(1),
        "__VIEWSTATEGENERATOR": viewgen.group(1),
        "__EVENTVALIDATION": eventval.group(1) if eventval else "",
        "ctl00$ContenidoMasterPlaceHolder$UserName": USERNAME,
        "ctl00$ContenidoMasterPlaceHolder$PasswordTextBox": PASSWORD,
        "ctl00$ContenidoMasterPlaceHolder$LoginButton": "Acceder"
    }

    resp = session.post(url, data=data)
    if "reservas" in resp.url.lower():
        print("✅ Login exitoso")
        enviar_telegram("✅ <b>Mr Mulligan iniciado</b>\nConectado al sistema de RSHECC")
        return True
    else:
        print("❌ Error en login")
        enviar_telegram("❌ Error en login de Mr Mulligan")
        return False

def buscar_y_reservar():
    fecha = (datetime.now() + timedelta(days=1)).strftime("%d/%m/%Y")
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Buscando en {fecha}...")

    # Aquí irá la petición real de búsqueda (pendiente de tu próxima captura)
    # Por ahora simulamos para probar todo el flujo
    # Cuando encontremos un hueco real, haremos la reserva automática

    # === SIMULACIÓN DE RESERVA EXITOSA ===
    mensaje = f"""🎉 <b>RESERVA REALIZADA AUTOMÁTICAMENTE</b>

Fecha: <b>{fecha}</b>
Tipo: Norte — 18 Hoyos
Hora: 08:12

Mr Mulligan ha reservado por ti ✅"""
    
    enviar_telegram(mensaje)
    print("🎯 Reserva simulada y notificada")

# ====================== BUCLE PRINCIPAL ======================
print("🚀 Mr Mulligan iniciado")

if login():
    while True:
        try:
            buscar_y_reservar()
            time.sleep(INTERVALO_SEGUNDOS)
        except Exception as e:
            print("Error:", e)
            time.sleep(60)
else:
    print("No se pudo iniciar sesión. Revisa credenciales.")
