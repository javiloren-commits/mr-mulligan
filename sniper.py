import json
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
DEPORTE_GOLF = "6"  # ID fijo del deporte golf en la API

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
        # El endpoint correcto es partidoscontactos
        # Respuesta: {"PartidosContactosResult": {"Valor": "{"Contactos":[...]}"}}
        # Valor es un string JSON que hay que parsear por separado
        url = f"{BASE_URL}/Jugadores/json/partidoscontactos/{CENTRO},{jugador_id},{PROCEDENCIA},{IDIOMA}"
        print(f"[contactos] GET {url}")
        r = requests.get(url, headers=HEADERS, timeout=10)
        print(f"[contactos] HTTP {r.status_code}")
        data = r.json()

        result = data.get("PartidosContactosResult", {})
        valor_str = result.get("Valor", "")

        # Valor es un string JSON — hay que parsearlo
        valor = json.loads(valor_str) if isinstance(valor_str, str) and valor_str else {}
        lista = valor.get("Contactos", []) or []

        contactos = []
        for j in lista:
            cid = j.get("Codigo") or j.get("codigo")
            nombre = j.get("Nombre") or j.get("nombre") or ""
            if cid and nombre:
                contactos.append({"id": cid, "nombre": nombre, "hcp": None})

        print(f"[contactos] Total: {len(contactos)}")
        return jsonify({"ok": True, "contactos": contactos})
    except Exception as e:
        print(f"[contactos] Error: {e}")
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

        print(f"[login] Respuesta: {str(data)[:2000]}")

        # La API puede devolver el resultado bajo distintas claves raíz
        result = (
            data.get("LoginResult") or
            data.get("loginResult") or
            data.get("Login") or
            data
        )

        status_ok = (
            result.get("StatusOK") or
            result.get("statusOK") or
            result.get("Status") == "OK"
        )

        if status_ok:
            jugador = result.get("Jugador") or result.get("DatosJugador") or {}
            # La API usa 'codigo' como ID del jugador
            jugador_id = (
                jugador.get("codigo") or jugador.get("IDJugador") or jugador.get("Id") or
                result.get("IDJugador") or result.get("IdJugador")
            )
            # Nombre completo: nombre + apellido
            nombre_parts = [jugador.get("nombre",""), jugador.get("apellido","")]
            nombre = " ".join(p for p in nombre_parts if p).strip() or usuario
            print(f"[login] OK - jugadorId={jugador_id} nombre={nombre}")
            return {"ok": True, "jugadorId": jugador_id, "nombre": nombre}
        else:
            msg = result.get("Mensaje") or result.get("mensaje") or "Credenciales incorrectas"
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
        # Estructura captura: jugadorId, fechaHora, centro, deporte(6=golf), procedencia, ?, ?, idioma, tipoInstalacion
        # El tipo de campo (11=Norte18, 13=Sur18...) va en la última posición
        url = f"{BASE_URL}/Reservas/json/instalacionesdia/{jugador_id},{fecha_fmt},{CENTRO},{DEPORTE_GOLF},{PROCEDENCIA},5,7,{IDIOMA},{tipo}"
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


def fecha_str(fecha, hora):
    """Construye FechaStr: YYYYMMDDHHММ"""
    return fecha.replace("-", "") + hora.replace(":", "")


def build_reserva_base(jugador_id, fecha, hora, cod_instalacion, jugadores, desmarcar_cobro=False, cod_jugador_paga=0, tipo_cobro=0, pago=None):
    """Construye el objeto reserva común a CrearReserva y PagarReserva."""
    componentes = [{"Codigo": jid} for jid in jugadores]
    return {
        "Centro": int(CENTRO),
        "DesmarcarCobro": desmarcar_cobro,
        "TipoJugador": 1,
        "Deporte": int(DEPORTE_GOLF),
        "JugadorFamiliar": 0,
        "CodJugadorProcesar": 0,
        "CodJugadorPaga": cod_jugador_paga,
        "TipoCobro": tipo_cobro,
        "Codigo": 0,
        "FechaStr": fecha_str(fecha, hora),
        "Instalacion": int(cod_instalacion),
        "Idioma": int(IDIOMA),
        "Pago": pago or [],
        "Componentes": componentes,
        "BuscarJugadores": False,
        "Tipo": 7,
        "TiempoJuego": 5,
        "Procedencia": int(PROCEDENCIA),
    }


def crear_reserva(usuario, clave, jugador_id, fecha, hora, cod_instalacion, jugadores):
    """
    Crea una reserva (PUT /Reservas/json/CrearReserva).
    Payload exacto extraído de captura Charles.
    """
    try:
        payload = {"reserva": build_reserva_base(jugador_id, fecha, hora, cod_instalacion, jugadores)}
        url = f"{BASE_URL}/Reservas/json/CrearReserva"
        print(f"[crear_reserva] PUT {url} payload={payload}")
        r = requests.put(url, headers={**HEADERS, "Content-Type": "application/json"},
                         json=payload, timeout=15)
        print(f"[crear_reserva] HTTP {r.status_code} - {r.text[:300]}")
        data = r.json()
        result = data.get("CrearReservaResult", {})
        if result.get("StatusOK"):
            reserva_id = result.get("Valor")  # Valor contiene el ID numérico
            print(f"[crear_reserva] ✅ Reserva creada ID={reserva_id} - {result.get('Mensaje','')}")
            return {"ok": True, "reservaId": reserva_id}
        else:
            msg = result.get("Mensaje") or "Error creando reserva"
            print(f"[crear_reserva] ❌ {msg}")
            return {"ok": False, "error": msg}
    except Exception as e:
        import traceback; traceback.print_exc()
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


def pagar_reserva(reserva_id, jugador_id, fecha, hora, cod_instalacion, jugadores, ticket_data):
    """
    Confirma y paga la reserva (PUT /Reservas/json/PagarReserva).
    Payload exacto extraído de captura Charles.
    FormaPago -2 = A CUENTA (domiciliación bancaria, sin coste inmediato).
    """
    try:
        pago = [{
            "Centro": int(CENTRO),
            "Idioma": int(IDIOMA),
            "Procedencia": int(PROCEDENCIA),
            "TransaccionTpv": {},
            "Tipo": 8,
            "LineaTicket": 0,
            "Jugador": jugador_id,
            "Importe": 0,
            "ASaldo": 0,
            "FormaPago": {
                "EsTarjeta": False,
                "EsAbonoAutorizado": False,
                "Codigo": -2,       # A CUENTA = domiciliación
                "EsMonedero": False,
                "EsAbono": False,
                "EsDeuda": False,
            }
        }]

        reserva = build_reserva_base(
            jugador_id, fecha, hora, cod_instalacion, jugadores,
            desmarcar_cobro=True,
            cod_jugador_paga=jugador_id,
            tipo_cobro=ticket_data.get("TipoCobro", 1),
            pago=pago,
        )
        reserva["Codigo"] = reserva_id  # ID de la reserva creada

        payload = {"reserva": reserva}
        url = f"{BASE_URL}/Reservas/json/PagarReserva"
        print(f"[pagar_reserva] PUT {url}")
        r = requests.put(url, headers={**HEADERS, "Content-Type": "application/json"},
                         json=payload, timeout=15)
        print(f"[pagar_reserva] HTTP {r.status_code} - {r.text[:300]}")
        data = r.json()
        result = data.get("PagarReservaResult", {})
        if result.get("StatusOK"):
            print(f"[pagar_reserva] ✅ {result.get('Mensaje','')}")
            return {"ok": True}
        else:
            msg = result.get("Mensaje") or "Error en el pago"
            print(f"[pagar_reserva] ❌ {msg}")
            return {"ok": False, "error": msg}
    except Exception as e:
        import traceback; traceback.print_exc()
        return {"ok": False, "error": str(e)}


def unix_date_to_hhmm(date_str):
    """
    Convierte /Date(1778221200000+0200)/ a HH:MM en hora local (Europe/Madrid).
    """
    import re
    m = re.search(r'/Date\((\d+)([+-]\d+)?\)/', str(date_str))
    if not m:
        return None
    ms = int(m.group(1))
    offset_str = m.group(2) or "+0000"
    # Calcular offset en minutos
    sign = 1 if offset_str[0] == "+" else -1
    offset_h = int(offset_str[1:3])
    offset_m = int(offset_str[3:5])
    offset_min = sign * (offset_h * 60 + offset_m)
    # Convertir a hora local
    total_min = ms // 1000 // 60 + offset_min
    h = (total_min // 60) % 24
    m2 = total_min % 60
    return f"{h:02d}:{m2:02d}"


def parsear_huecos(instalaciones_data, desde, hasta):
    """
    Extrae huecos disponibles dentro de la franja horaria.
    La API devuelve lista directa en InstalacionesDiaResult.
    Todos los items son huecos disponibles (los ocupados no aparecen).
    La hora viene en formato Unix: /Date(timestamp+offset)/
    """
    huecos = []
    try:
        desde_min = time_to_minutes(desde)
        hasta_min = time_to_minutes(hasta)

        # La API devuelve: {"InstalacionesDiaResult": [ {codigo, hora, ...}, ... ]}
        candidatos = instalaciones_data.get("InstalacionesDiaResult", []) or []
        if isinstance(candidatos, dict):
            # Por si acaso viene como dict
            candidatos = list(candidatos.values())

        print(f"[parsear_huecos] Total huecos recibidos: {len(candidatos)}")

        for h in candidatos:
            hora_raw = h.get("hora") or h.get("Hora") or ""
            if not hora_raw:
                continue

            hora_str = unix_date_to_hhmm(hora_raw)
            if not hora_str:
                continue

            hora_min = time_to_minutes(hora_str)
            if desde_min <= hora_min <= hasta_min:
                # Guardar hora y codigo de instalación para reservar
                huecos.append({
                    "hora": hora_str,
                    "cod_instalacion": h.get("codigo"),
                    "descripcion": h.get("descripcion", ""),
                })

        huecos.sort(key=lambda x: x["hora"])
        print(f"[parsear_huecos] Huecos en franja {desde}-{hasta}: {[h['hora'] for h in huecos]}")

    except Exception as e:
        print(f"[parsear_huecos] Error: {e}")
        import traceback
        traceback.print_exc()

    return huecos


def filtrar_por_instalacion(huecos, tipo):
    """Filtra huecos para que coincidan con el tipo de campo solicitado."""
    filtrados = [h for h in huecos if h["cod_instalacion"] == tipo]
    if filtrados:
        print(f"[filtrar] {len(filtrados)} huecos para instalación {tipo}")
        return filtrados
    print(f"[filtrar] Sin huecos para instalación {tipo}")
    return []


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

def sniper_loop(fecha, params):
    """Bucle independiente por fecha. Lee/escribe solo su propio estado en snipers[fecha]."""

    def set_state(**kwargs):
        with snipers_lock:
            if fecha in snipers:
                snipers[fecha].update(kwargs)

    usuario = params["usuario"]
    clave = params["clave"]
    jugador_id = params["jugadorId"]
    tipo = params["tipo"]
    desde = params["desde"]
    hasta = params["hasta"]
    jugadores = params["jugadores"]

    with snipers_lock:
        stop_event = snipers[fecha]["stop_event"]
        poll_interval = snipers[fecha]["poll_interval"]

    tipo_nombre = {11: "Norte 18h", 12: "Norte 9h", 13: "Sur 18h", 14: "Sur 9h", 15: "Pares 3"}
    print(f"[Sniper:{fecha}] Iniciando. Tipo:{tipo} {desde}-{hasta} Jugadores:{jugadores} Intervalo:{poll_interval}s")

    while not stop_event.is_set():
        with snipers_lock:
            poll_interval = snipers[fecha]["poll_interval"]
            snipers[fecha]["attempts"] += 1
            intento = snipers[fecha]["attempts"]

        print(f"[Sniper:{fecha}] Intento #{intento}")
        set_state(mensaje=f"Buscando huecos en {desde}–{hasta}… (cada {poll_interval}s)")

        instalaciones = get_instalaciones_dia(usuario, clave, jugador_id, fecha, tipo)
        if instalaciones is None:
            set_state(mensaje="Error consultando disponibilidad")
            stop_event.wait(poll_interval)
            continue

        huecos = parsear_huecos(instalaciones, desde, hasta)
        huecos = filtrar_por_instalacion(huecos, tipo)
        print(f"[Sniper:{fecha}] Huecos tipo {tipo}: {[h['hora'] for h in huecos]}")

        if not huecos:
            set_state(mensaje=f"Sin huecos. Reintentando en {poll_interval}s…")
            stop_event.wait(poll_interval)
            continue

        hueco = huecos[0]
        hora = hueco["hora"]
        cod_instalacion = hueco["cod_instalacion"]
        set_state(status="found", mensaje=f"¡Hueco a las {hora}! Reservando…")
        print(f"[Sniper:{fecha}] Hueco a las {hora} (instalación {cod_instalacion}). Reservando...")

        res_crear = crear_reserva(usuario, clave, jugador_id, fecha, hora, cod_instalacion, jugadores)
        if not res_crear["ok"]:
            print(f"[Sniper:{fecha}] Error reserva: {res_crear['error']}")
            set_state(status="searching", mensaje=f"Error: {res_crear['error']}. Reintentando…")
            stop_event.wait(5)
            continue

        reserva_id = res_crear["reservaId"]
        ticket_res = crear_ticket(reserva_id, jugador_id)
        if not ticket_res["ok"]:
            set_state(status="searching")
            stop_event.wait(5)
            continue

        pago_res = pagar_reserva(reserva_id, jugador_id, fecha, hora, cod_instalacion, jugadores, ticket_res["ticket"])
        if not pago_res["ok"]:
            set_state(status="searching")
            stop_event.wait(5)
            continue

        fecha_fmt = datetime.strptime(fecha, "%Y-%m-%d").strftime("%d/%m/%Y")
        set_state(
            status="reserved",
            mensaje="¡Reserva confirmada!",
            reserva={"id": reserva_id, "fecha": fecha_fmt, "hora": hora, "tipo": tipo, "jugadores": jugadores},
        )
        print(f"[Sniper:{fecha}] ✅ CONFIRMADA: {fecha_fmt} {hora} {tipo_nombre.get(tipo, tipo)}")
        send_telegram(
            f"⛳ <b>¡Reserva confirmada!</b>\n\n"
            f"📅 <b>Fecha:</b> {fecha_fmt}\n"
            f"🕐 <b>Hora:</b> {hora}\n"
            f"🏌️ <b>Campo:</b> {tipo_nombre.get(tipo, str(tipo))}\n"
            f"👥 <b>Jugadores:</b> {len(jugadores)}\n"
            f"🔖 <b>Ref:</b> #{reserva_id}"
        )
        break

    with snipers_lock:
        if fecha in snipers and snipers[fecha]["status"] == "searching":
            snipers[fecha]["status"] = "idle"
            snipers[fecha]["mensaje"] = "Búsqueda detenida"
    print(f"[Sniper:{fecha}] Finalizado")


# ══════════════════════════════════════════════════════
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
