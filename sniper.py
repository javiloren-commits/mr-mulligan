"""
Mr. Mulligan – Backend Flask
RSHECC Golf Auto-Booking Sniper
Desplegado en Render.com
"""

import os
import threading
import time
import requests
from datetime import datetime, timedelta
from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

# ══════════════════════════════════════════════════════
#  CONFIGURACIÓN
# ══════════════════════════════════════════════════════
BASE_URL = "https://reservas.rshecc.es/AppWebServices.7.0.0"
CENTRO = "24"
PROCEDENCIA = "6"
IDIOMA = "1"

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID = os.environ.get("CHAT_ID", "")

HEADERS = {
    "Authorization": "Basic d2Vic2VydmljZTpTZWd1cmlkYWQqd2ViMTY=",
    "User-Agent": "StartMasterRSHECC/1 CFNetwork/3860.400.51 Darwin/25.3.0",
    "Accept": "*/*",
    "Accept-Language": "es-ES,es;q=0.9",
    "Cache-Control": "max-age=0;no-cache;no-store",
}

POLL_INTERVAL = 30  # segundos entre intentos

# ══════════════════════════════════════════════════════
#  ESTADO GLOBAL DEL SNIPER
# ══════════════════════════════════════════════════════
sniper_state = {
    "status": "idle",       # idle | searching | found | reserved | error
    "attempts": 0,
    "mensaje": "",
    "error": "",
    "reserva": None,        # dict con detalles de la reserva confirmada
    "params": None,
    "thread": None,
    "stop_event": threading.Event(),
}


# ══════════════════════════════════════════════════════
#  ENDPOINTS FLASK
# ══════════════════════════════════════════════════════

@app.route("/", methods=["GET"])
def health():
    return jsonify({"ok": True, "service": "Mr. Mulligan", "status": sniper_state["status"]})


@app.route("/login", methods=["POST"])
def login_endpoint():
    """Valida credenciales contra la API del club y devuelve datos del jugador."""
    body = request.get_json()
    usuario = body.get("usuario", "").strip()
    clave = body.get("clave", "").strip()

    if not usuario or not clave:
        return jsonify({"ok": False, "error": "Faltan credenciales"})

    resultado = do_login(usuario, clave)
    if resultado["ok"]:
        return jsonify(resultado)
    else:
        return jsonify(resultado), 401


@app.route("/contactos", methods=["POST"])
def contactos_endpoint():
    """Devuelve los contactos (partidos) del jugador."""
    body = request.get_json()
    usuario = body.get("usuario", "")
    clave = body.get("clave", "")
    jugador_id = body.get("jugadorId", "")

    try:
        url = f"{BASE_URL}/Jugadores/json/partidoscontactos/{CENTRO},{jugador_id},{PROCEDENCIA},{IDIOMA}"
        r = requests.get(url, headers=auth_headers(usuario, clave), timeout=10)
        data = r.json()

        # Parsear contactos
        contactos = []
        result = data.get("PartidosContactosResult", {})
        lista = result.get("Jugadores", []) or []
        for j in lista:
            contactos.append({
                "id": j.get("IDJugador"),
                "nombre": j.get("NombreCompleto", ""),
                "hcp": j.get("Handicap"),
            })

        return jsonify({"ok": True, "contactos": contactos})
    except Exception as e:
        return jsonify({"ok": False, "contactos": [], "error": str(e)})


@app.route("/start", methods=["POST"])
def start_sniper():
    """Lanza el sniper en background."""
    global sniper_state

    # Si ya hay uno corriendo, detenerlo
    if sniper_state["status"] == "searching":
        sniper_state["stop_event"].set()
        time.sleep(1)

    body = request.get_json()
    required = ["usuario", "clave", "jugadorId", "fecha", "tipo", "desde", "hasta", "jugadores"]
    for field in required:
        if field not in body:
            return jsonify({"ok": False, "error": f"Falta campo: {field}"})

    # Resetear estado
    sniper_state["stop_event"].clear()
    sniper_state["status"] = "searching"
    sniper_state["attempts"] = 0
    sniper_state["mensaje"] = "Iniciando..."
    sniper_state["error"] = ""
    sniper_state["reserva"] = None
    sniper_state["params"] = body

    # Lanzar thread
    thread = threading.Thread(target=sniper_loop, args=(body,), daemon=True)
    sniper_state["thread"] = thread
    thread.start()

    return jsonify({"ok": True, "mensaje": "Sniper iniciado"})


@app.route("/status", methods=["GET"])
def get_status():
    """Devuelve el estado actual del sniper."""
    return jsonify({
        "status": sniper_state["status"],
        "attempts": sniper_state["attempts"],
        "mensaje": sniper_state["mensaje"],
        "error": sniper_state["error"],
        "reserva": sniper_state["reserva"],
    })


@app.route("/stop", methods=["POST"])
def stop_sniper():
    """Detiene el sniper."""
    sniper_state["stop_event"].set()
    sniper_state["status"] = "idle"
    sniper_state["mensaje"] = "Búsqueda cancelada"
    return jsonify({"ok": True})


@app.route("/anular", methods=["POST"])
def anular_endpoint():
    """Anula una reserva existente."""
    body = request.get_json()
    usuario = body.get("usuario", "")
    clave = body.get("clave", "")
    reserva_id = body.get("reservaId", "")

    try:
        url = f"{BASE_URL}/Reservas/json/AnularReserva/{reserva_id},{PROCEDENCIA},{IDIOMA}"
        r = requests.get(url, headers=auth_headers(usuario, clave), timeout=10)
        data = r.json()

        # La respuesta de anulación tiene AnularReservaResult
        result = data.get("AnularReservaResult", {})
        if result.get("StatusOK"):
            return jsonify({"ok": True})
        else:
            msg = result.get("Mensaje", "No se pudo anular")
            return jsonify({"ok": False, "error": msg})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


# ══════════════════════════════════════════════════════
#  LÓGICA DE LA API DEL CLUB
# ══════════════════════════════════════════════════════

def auth_headers(usuario=None, clave=None):
    """Headers con Basic Auth de la API del club."""
    h = dict(HEADERS)
    return h


def do_login(usuario, clave):
    """Login real contra la API del club."""
    try:
        url = f"{BASE_URL}/Jugadores/json/login"
        params = {
            "centro": CENTRO,
            "usuario": usuario,
            "clave": clave,
            "procedencia": PROCEDENCIA,
            "idioma": IDIOMA,
        }
        r = requests.get(url, headers=HEADERS, params=params, timeout=10)
        data = r.json()

        result = data.get("LoginResult", {})
        if result.get("StatusOK"):
            jugador = result.get("Jugador", {})
            jugador_id = jugador.get("IDJugador") or result.get("IDJugador")
            nombre = jugador.get("NombreCompleto") or jugador.get("Nombre") or usuario
            return {
                "ok": True,
                "jugadorId": jugador_id,
                "nombre": nombre,
            }
        else:
            msg = result.get("Mensaje", "Credenciales incorrectas")
            return {"ok": False, "error": msg}
    except Exception as e:
        return {"ok": False, "error": f"Error de conexión: {str(e)}"}


def get_instalaciones_dia(usuario, clave, jugador_id, fecha, tipo):
    """
    Obtiene huecos disponibles para una fecha y tipo de campo.
    Captura real: /instalacionesdia/33894,202605080000,24,6,6,5,7,1,4
    Parámetros:   jugadorId, fechaHora, centro, deporte, procedencia, ?, ?, idioma, ?
    """
    try:
        fecha_fmt = fecha.replace("-", "") + "0000"
        # Usando exactamente la misma estructura que la captura de Charles,
        # solo cambiando jugadorId, fechaHora y tipo de deporte
        url = f"{BASE_URL}/Reservas/json/instalacionesdia/{jugador_id},{fecha_fmt},{CENTRO},{tipo},{PROCEDENCIA},5,7,{IDIOMA},4"
        print(f"[instalacionesdia] GET {url}")
        r = requests.get(url, headers=HEADERS, timeout=15)
        print(f"[instalacionesdia] HTTP {r.status_code}")
        print(f"[instalacionesdia] Respuesta: {r.text[:1000]}")
        data = r.json()
        return data
    except Exception as e:
        print(f"[instalacionesdia] Error: {e}")
        return None


def get_tiempos(fecha, tipo):
    """Obtiene los tiempos (slots horarios) disponibles."""
    try:
        fecha_fmt = fecha.replace("-", "")
        url = f"{BASE_URL}/Reservas/json/tiempos/{fecha_fmt},{CENTRO},{tipo},{PROCEDENCIA},0"
        r = requests.get(url, headers=HEADERS, timeout=10)
        return r.json()
    except Exception as e:
        print(f"[tiempos] Error: {e}")
        return None


def crear_reserva(usuario, clave, jugador_id, fecha, hora, tipo, jugadores):
    """
    Crea una reserva (PUT /Reservas/json/CrearReserva).
    Devuelve el ID de reserva si tiene éxito.
    """
    try:
        fecha_fmt = fecha.replace("-", "")
        hora_fmt = hora.replace(":", "")

        # Construir lista de jugadores
        jugadores_payload = []
        for i, jid in enumerate(jugadores):
            jugadores_payload.append({
                "IDJugador": jid,
                "Orden": i + 1,
                "EsTitular": i == 0
            })

        payload = {
            "CrearReservaRequest": {
                "IDCentro": int(CENTRO),
                "IDDeporte": int(tipo),
                "IDProcedencia": int(PROCEDENCIA),
                "Fecha": fecha_fmt,
                "Hora": hora_fmt,
                "IDJugadorPrincipal": jugador_id,
                "Jugadores": jugadores_payload,
                "IDIdioma": int(IDIOMA),
            }
        }

        url = f"{BASE_URL}/Reservas/json/CrearReserva"
        r = requests.put(url, headers={**HEADERS, "Content-Type": "application/json"},
                         json=payload, timeout=15)
        data = r.json()
        result = data.get("CrearReservaResult", {})

        if result.get("StatusOK"):
            return {"ok": True, "reservaId": result.get("IDReserva")}
        else:
            return {"ok": False, "error": result.get("Mensaje", "Error creando reserva")}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def crear_ticket(reserva_id, jugador_id):
    """Crea el ticket de pago para la reserva."""
    try:
        # Parámetros de la captura: 881017,0,33894,1,6,1
        # reservaId, ?, jugadorId, ?, procedencia, idioma
        url = f"{BASE_URL}/Reservas/json/crearticket/{reserva_id},0,{jugador_id},{IDIOMA},{PROCEDENCIA},{IDIOMA}"
        r = requests.get(url, headers=HEADERS, timeout=10)
        data = r.json()
        result = data.get("CrearTicketResult", {})
        if result.get("StatusOK"):
            return {"ok": True, "ticket": result}
        else:
            return {"ok": False, "error": result.get("Mensaje", "Error creando ticket")}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def pagar_reserva(reserva_id, jugador_id, ticket_data):
    """Confirma y paga la reserva."""
    try:
        payload = {
            "PagarReservaRequest": {
                "IDReserva": reserva_id,
                "IDJugador": jugador_id,
                "IDProcedencia": int(PROCEDENCIA),
                "IDIdioma": int(IDIOMA),
                "FormaPago": 1,  # Pago contra cuenta del socio
                "Ticket": ticket_data,
            }
        }

        url = f"{BASE_URL}/Reservas/json/PagarReserva"
        r = requests.put(url, headers={**HEADERS, "Content-Type": "application/json"},
                         json=payload, timeout=15)
        data = r.json()
        result = data.get("PagarReservaResult", {})

        if result.get("StatusOK"):
            return {"ok": True}
        else:
            return {"ok": False, "error": result.get("Mensaje", "Error en el pago")}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def parsear_huecos(instalaciones_data, desde, hasta):
    """
    Extrae huecos disponibles dentro de la franja horaria.
    Prueba múltiples estructuras posibles del JSON.
    """
    huecos = []
    print(f"[parsear_huecos] Claves raíz: {list(instalaciones_data.keys())}")

    try:
        desde_min = time_to_minutes(desde)
        hasta_min = time_to_minutes(hasta)

        # Buscar la clave principal del resultado (puede variar)
        result = None
        for key in instalaciones_data:
            if "Result" in key or "result" in key or "Instalacion" in key:
                result = instalaciones_data[key]
                print(f"[parsear_huecos] Usando clave: {key}")
                break

        if result is None:
            result = instalaciones_data
            print(f"[parsear_huecos] Usando raíz directamente")

        print(f"[parsear_huecos] Tipo result: {type(result)}, claves: {list(result.keys()) if isinstance(result, dict) else 'lista'}")

        # Buscar lista de huecos/horarios en distintas estructuras posibles
        candidatos = []

        if isinstance(result, dict):
            # Opción A: result tiene lista "Instalaciones"
            if "Instalaciones" in result:
                for inst in (result["Instalaciones"] or []):
                    for h in (inst.get("Horarios") or inst.get("Huecos") or []):
                        candidatos.append(h)

            # Opción B: result tiene lista "Horarios" directa
            elif "Horarios" in result:
                candidatos = result["Horarios"] or []

            # Opción C: result tiene lista "Huecos"
            elif "Huecos" in result:
                candidatos = result["Huecos"] or []

            # Opción D: buscar cualquier lista dentro del result
            else:
                for key, val in result.items():
                    if isinstance(val, list) and len(val) > 0:
                        print(f"[parsear_huecos] Lista encontrada en clave '{key}': {len(val)} items")
                        # Mostrar primer elemento para entender estructura
                        print(f"[parsear_huecos] Primer item: {val[0]}")
                        candidatos = val
                        break

        elif isinstance(result, list):
            candidatos = result

        print(f"[parsear_huecos] Candidatos encontrados: {len(candidatos)}")
        if candidatos:
            print(f"[parsear_huecos] Ejemplo de candidato: {candidatos[0]}")

        for h in candidatos:
            if not isinstance(h, dict):
                continue

            # Buscar campo de hora (distintos nombres posibles)
            hora_str = (
                h.get("Hora") or h.get("hora") or
                h.get("HoraInicio") or h.get("Horario") or
                h.get("Time") or ""
            )
            if not hora_str:
                continue

            hora_str = str(hora_str)
            # Normalizar: HHMM → HH:MM
            if ":" not in hora_str and len(hora_str) == 4:
                hora_str = hora_str[:2] + ":" + hora_str[2:]

            hora_min = time_to_minutes(hora_str)

            # Buscar campo disponibilidad (distintos nombres posibles)
            disponible = (
                h.get("Disponible") or h.get("disponible") or
                h.get("EsLibre") or h.get("Libre") or
                h.get("Available") or False
            )

            if disponible and desde_min <= hora_min <= hasta_min:
                huecos.append(hora_str)

        huecos.sort()
        print(f"[parsear_huecos] Huecos en franja {desde}-{hasta}: {huecos}")

    except Exception as e:
        print(f"[parsear_huecos] Error: {e}")
        import traceback
        traceback.print_exc()

    return huecos


def time_to_minutes(t):
    """Convierte HH:MM a minutos totales."""
    try:
        parts = t.split(":")
        return int(parts[0]) * 60 + int(parts[1])
    except:
        return 0


def send_telegram(mensaje):
    """Envía notificación por Telegram."""
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("[Telegram] No configurado")
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, json={
            "chat_id": CHAT_ID,
            "text": mensaje,
            "parse_mode": "HTML"
        }, timeout=5)
    except Exception as e:
        print(f"[Telegram] Error: {e}")


# ══════════════════════════════════════════════════════
#  BUCLE PRINCIPAL DEL SNIPER
# ══════════════════════════════════════════════════════

def sniper_loop(params):
    """Bucle que busca y reserva continuamente hasta encontrar hueco o ser detenido."""
    global sniper_state

    usuario = params["usuario"]
    clave = params["clave"]
    jugador_id = params["jugadorId"]
    fecha = params["fecha"]
    tipo = params["tipo"]
    desde = params["desde"]
    hasta = params["hasta"]
    jugadores = params["jugadores"]

    print(f"[Sniper] Iniciando. Fecha:{fecha} Tipo:{tipo} {desde}-{hasta} Jugadores:{jugadores}")

    while not sniper_state["stop_event"].is_set():
        sniper_state["attempts"] += 1
        intento = sniper_state["attempts"]

        print(f"[Sniper] Intento #{intento}")
        sniper_state["mensaje"] = f"Buscando huecos en {desde}–{hasta}…"

        # 1. Buscar huecos disponibles
        instalaciones = get_instalaciones_dia(usuario, clave, jugador_id, fecha, tipo)

        if instalaciones is None:
            sniper_state["mensaje"] = "Error consultando disponibilidad"
            time.sleep(POLL_INTERVAL)
            continue

        huecos = parsear_huecos(instalaciones, desde, hasta)
        print(f"[Sniper] Huecos encontrados: {huecos}")

        if not huecos:
            sniper_state["mensaje"] = f"Sin huecos disponibles. Reintentando en {POLL_INTERVAL}s…"
            time.sleep(POLL_INTERVAL)
            continue

        # 2. ¡Hueco encontrado! Intentar reservar el primero (más temprano)
        hora = huecos[0]
        sniper_state["status"] = "found"
        sniper_state["mensaje"] = f"¡Hueco a las {hora}! Reservando…"
        print(f"[Sniper] Hueco encontrado a las {hora}. Reservando...")

        # 3. Crear reserva
        res_crear = crear_reserva(usuario, clave, jugador_id, fecha, hora, tipo, jugadores)

        if not res_crear["ok"]:
            print(f"[Sniper] Error creando reserva: {res_crear['error']}")
            sniper_state["status"] = "searching"
            sniper_state["mensaje"] = f"Hueco ocupado ({res_crear['error']}). Buscando otro…"
            time.sleep(5)
            continue

        reserva_id = res_crear["reservaId"]
        print(f"[Sniper] Reserva creada: ID={reserva_id}")

        # 4. Crear ticket
        ticket_res = crear_ticket(reserva_id, jugador_id)
        if not ticket_res["ok"]:
            print(f"[Sniper] Error ticket: {ticket_res['error']}")
            # Continuar buscando
            sniper_state["status"] = "searching"
            time.sleep(5)
            continue

        # 5. Pagar/confirmar reserva
        pago_res = pagar_reserva(reserva_id, jugador_id, ticket_res["ticket"])
        if not pago_res["ok"]:
            print(f"[Sniper] Error pago: {pago_res['error']}")
            sniper_state["status"] = "searching"
            time.sleep(5)
            continue

        # ¡ÉXITO!
        tipo_nombre = {11: "Norte 18h", 12: "Norte 9h", 13: "Sur 18h", 14: "Sur 9h", 15: "Pares 3"}
        fecha_fmt = datetime.strptime(fecha, "%Y-%m-%d").strftime("%d/%m/%Y")

        sniper_state["status"] = "reserved"
        sniper_state["reserva"] = {
            "id": reserva_id,
            "fecha": fecha_fmt,
            "hora": hora,
            "tipo": tipo,
            "jugadores": jugadores,
        }
        sniper_state["mensaje"] = "¡Reserva confirmada!"

        print(f"[Sniper] ✅ RESERVA CONFIRMADA: {fecha_fmt} {hora} {tipo_nombre.get(tipo, tipo)}")

        # Notificar por Telegram
        msg_telegram = (
            f"⛳ <b>¡Reserva confirmada!</b>\n\n"
            f"📅 <b>Fecha:</b> {fecha_fmt}\n"
            f"🕐 <b>Hora:</b> {hora}\n"
            f"🏌️ <b>Campo:</b> {tipo_nombre.get(tipo, str(tipo))}\n"
            f"👥 <b>Jugadores:</b> {len(jugadores)}\n"
            f"🔖 <b>Ref:</b> #{reserva_id}"
        )
        send_telegram(msg_telegram)

        break  # Salir del bucle

    if sniper_state["status"] == "searching":
        sniper_state["status"] = "idle"
        sniper_state["mensaje"] = "Búsqueda detenida"

    print(f"[Sniper] Finalizado con estado: {sniper_state['status']}")


# ══════════════════════════════════════════════════════
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
