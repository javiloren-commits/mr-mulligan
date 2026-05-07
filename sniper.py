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

POLL_INTERVAL_DEFAULT = 30  # segundos entre intentos

# ══════════════════════════════════════════════════════
#  ESTADO GLOBAL — múltiples snipers indexados por fecha
# ══════════════════════════════════════════════════════
snipers = {}
snipers_lock = __import__('threading').Lock()

def make_sniper_state():
    return {
        "status": "idle",
        "attempts": 0,
        "mensaje": "",
        "error": "",
        "reserva": None,
        "params": None,
        "thread": None,
        "stop_event": __import__('threading').Event(),
        "poll_interval": POLL_INTERVAL_DEFAULT,
    }


# ══════════════════════════════════════════════════════
#  ENDPOINTS FLASK
# ══════════════════════════════════════════════════════

@app.route("/", methods=["GET"])
def health():
    with snipers_lock:
        resumen = {f: {"status": s["status"], "attempts": s["attempts"]} for f, s in snipers.items()}
    return jsonify({"ok": True, "service": "Mr. Mulligan", "snipers": resumen})


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
    """Lanza un sniper para una fecha concreta. Permite multiples en paralelo."""
    body = request.get_json()
    required = ["usuario", "clave", "jugadorId", "fecha", "tipo", "desde", "hasta", "jugadores"]
    for field in required:
        if field not in body:
            return jsonify({"ok": False, "error": f"Falta campo: {field}"})

    fecha = body["fecha"]
    poll_interval = int(body.get("pollInterval", POLL_INTERVAL_DEFAULT))
    poll_interval = max(10, min(300, poll_interval))

    with snipers_lock:
        if fecha in snipers and snipers[fecha]["status"] == "searching":
            snipers[fecha]["stop_event"].set()
            time.sleep(0.5)
        state = make_sniper_state()
        state["status"] = "searching"
        state["mensaje"] = "Iniciando..."
        state["params"] = body
        state["poll_interval"] = poll_interval
        snipers[fecha] = state

    thread = threading.Thread(target=sniper_loop, args=(fecha, body), daemon=True)
    with snipers_lock:
        snipers[fecha]["thread"] = thread
    thread.start()

    return jsonify({"ok": True, "fecha": fecha, "pollInterval": poll_interval, "mensaje": f"Sniper iniciado para {fecha}"})


@app.route("/status", methods=["GET"])
def get_status():
    """Devuelve estado de todos los snipers, o uno concreto con ?fecha=."""
    fecha = request.args.get("fecha")
    with snipers_lock:
        if fecha:
            if fecha not in snipers:
                return jsonify({"status": "idle", "attempts": 0, "mensaje": "", "error": "", "reserva": None})
            s = snipers[fecha]
            return jsonify({"fecha": fecha, "status": s["status"], "attempts": s["attempts"],
                            "mensaje": s["mensaje"], "error": s["error"], "reserva": s["reserva"],
                            "pollInterval": s["poll_interval"], "params": s["params"]})
        result = {}
        for f, s in snipers.items():
            result[f] = {"status": s["status"], "attempts": s["attempts"], "mensaje": s["mensaje"],
                         "error": s["error"], "reserva": s["reserva"], "pollInterval": s["poll_interval"],
                         "params": s["params"]}
    return jsonify(result)


@app.route("/stop", methods=["POST"])
def stop_sniper():
    """Detiene el sniper de una fecha o todos."""
    body = request.get_json() or {}
    fecha = body.get("fecha")
    with snipers_lock:
        targets = [fecha] if fecha and fecha in snipers else list(snipers.keys())
        for f in targets:
            snipers[f]["stop_event"].set()
            snipers[f]["status"] = "idle"
            snipers[f]["mensaje"] = "Busqueda cancelada"
    return jsonify({"ok": True, "detenidos": targets})


@app.route("/fechas", methods=["POST"])
def fechas_endpoint():
    """Devuelve las fechas disponibles para reservar."""
    try:
        url = f"{BASE_URL}/Reservas/json/diaspermitidos/{CENTRO},{DEPORTE_GOLF},{PROCEDENCIA}"
        r = requests.get(url, headers=HEADERS, timeout=10)
        data = r.json()
        import re, datetime as dt
        dias = data.get("DiasPermitidosResult", []) or []
        fechas = []
        for d in dias:
            # API devuelve lista de strings: "/Date(1778104800000+0200)/"
            hora_raw = d if isinstance(d, str) else (d.get("fecha") or d.get("Fecha") or "") if isinstance(d, dict) else ""
            if hora_raw and "/Date(" in hora_raw:
                m = re.search(r"/Date\((\d+)", hora_raw)
                if m:
                    ts = int(m.group(1)) // 1000
                    fecha_dt = dt.datetime.utcfromtimestamp(ts) + dt.timedelta(hours=2)
                    fechas.append(fecha_dt.strftime("%Y-%m-%d"))
        print(f"[fechas] {len(fechas)} fechas disponibles: {fechas[:5]}")
        return jsonify({"ok": True, "fechas": fechas})
    except Exception as e:
        print(f"[fechas] Error: {e}")
        return jsonify({"ok": False, "fechas": []})


@app.route("/validar_jugadores", methods=["POST"])
def validar_jugadores_endpoint():
    """Verifica qué jugadores ya tienen reserva en una fecha concreta."""
    body = request.get_json()
    jugador_id = body.get("jugadorId")
    fecha = body.get("fecha", "")
    ids = body.get("ids", [])
    fecha_fmt = fecha.replace("-", "")

    ocupados = []
    for jid in ids:
        try:
            url = f"{BASE_URL}/Reservas/json/validarjugador/{CENTRO},{DEPORTE_GOLF},{PROCEDENCIA},{fecha_fmt},{jid},{IDIOMA}"
            r = requests.get(url, headers=HEADERS, timeout=8)
            data = r.json()
            result = data.get("ValidarJugadorResult", {})
            # Si tiene reserva ese día, el resultado indica que NO puede reservar
            if not result.get("StatusOK", True) or result.get("TieneReserva") or result.get("Valor") == False:
                ocupados.append(jid)
        except Exception:
            pass

    print(f"[validar_jugadores] fecha={fecha} ocupados={ocupados}")
    return jsonify({"ok": True, "ocupados": ocupados})


@app.route("/reservas", methods=["POST"])
def reservas_endpoint():
    """Devuelve las reservas actuales del jugador."""
    body = request.get_json()
    jugador_id = body.get("jugadorId")
    reservas = get_reservas(jugador_id)
    # Limpiar snipers cuya reserva fue cancelada externamente
    ids_activos = {r["id"] for r in reservas if r.get("id")}
    with snipers_lock:
        for key, s in list(snipers.items()):
            if s["status"] == "reserved" and s.get("reserva"):
                rid = s["reserva"].get("id")
                if rid and rid not in ids_activos:
                    print(f"[reservas] Reserva #{rid} cancelada externamente, limpiando {key}")
                    s["status"] = "cancelled"
                    s["mensaje"] = "Reserva cancelada externamente"
                    s["reserva"] = None

    return jsonify({"ok": True, "reservas": reservas})


@app.route("/mover", methods=["POST"])
def mover_endpoint():
    """Inicia un sniper de mejora para una reserva existente."""
    body = request.get_json()
    required = ["usuario", "clave", "jugadorId", "reservaId", "fecha",
                "hora_actual", "cod_instalacion_actual", "tipo", "desde", "hasta", "jugadores"]
    for field in required:
        if field not in body:
            return jsonify({"ok": False, "error": f"Falta campo: {field}"})

    reserva_id = body["reservaId"]
    fecha = body["fecha"]
    poll_interval = int(body.get("pollInterval", 60))
    poll_interval = max(10, min(300, poll_interval))

    # Verificar que la reserva es modificable
    if not es_modificable(reserva_id):
        return jsonify({"ok": False, "error": "Esta reserva no se puede modificar"})

    # Usar clave única: "mejora_{reservaId}" para no colisionar con snipers de reserva nueva
    key = f"mejora_{reserva_id}"

    with snipers_lock:
        if key in snipers and snipers[key]["status"] == "searching":
            snipers[key]["stop_event"].set()
            time.sleep(0.5)
        state = make_sniper_state()
        state["status"] = "searching"
        state["mensaje"] = "Buscando mejora..."
        state["params"] = body
        state["poll_interval"] = poll_interval
        snipers[key] = state

    thread = threading.Thread(target=mejora_loop, args=(key, body), daemon=True)
    with snipers_lock:
        snipers[key]["thread"] = thread
    thread.start()

    return jsonify({"ok": True, "key": key, "mensaje": f"Buscando mejora para reserva #{reserva_id}"})


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



def get_instalaciones_dia_single(jugador_id, fecha_fmt, tipo_int):
    url = f"{BASE_URL}/Reservas/json/instalacionesdia/{jugador_id},{fecha_fmt},{CENTRO},{DEPORTE_GOLF},{PROCEDENCIA},5,7,{IDIOMA},{tipo_int}"
    print(f"[instalacionesdia] GET {url}")
    r = requests.get(url, headers=HEADERS, timeout=15)
    print(f"[instalacionesdia] HTTP {r.status_code}")
    if r.status_code != 200:
        print(f"[instalacionesdia] Error: {r.text[:200]}")
        return []
    try:
        data = r.json()
        return data.get("InstalacionesDiaResult", []) or []
    except Exception as e:
        print(f"[instalacionesdia] Parse error: {e}")
        return []


def get_instalaciones_dia(usuario, clave, jugador_id, fecha, tipo):
    try:
        fecha_fmt = fecha.replace("-", "") + "0000"
        if isinstance(tipo, str) and "," in tipo:
            tipos = [int(t) for t in tipo.split(",")]
            todos = []
            for t in tipos:
                todos.extend(get_instalaciones_dia_single(jugador_id, fecha_fmt, t))
            print(f"[instalacionesdia] Dual {tipos}: {len(todos)} huecos totales")
            return {"InstalacionesDiaResult": todos}
        else:
            tipo_int = int(tipo) if isinstance(tipo, str) else tipo
            huecos = get_instalaciones_dia_single(jugador_id, fecha_fmt, tipo_int)
            return {"InstalacionesDiaResult": huecos}
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


def es_modificable(reserva_id):
    """Verifica si una reserva puede modificarse."""
    try:
        url = f"{BASE_URL}/Reservas/json/esmodificable/{reserva_id},{PROCEDENCIA}"
        r = requests.get(url, headers=HEADERS, timeout=10)
        data = r.json()
        return data.get("EsModificableResult", False)
    except Exception as e:
        print(f"[esmodificable] Error: {e}")
        return False


def mover_reserva(reserva_id, jugador_id, fecha, hora_nueva, cod_instalacion_nueva, jugadores):
    """
    Mueve una reserva existente a un nuevo horario/campo.
    PUT /Reservas/json/moverreserva — mismo payload que CrearReserva pero con Codigo=reservaId.
    """
    try:
        reserva = build_reserva_base(jugador_id, fecha, hora_nueva, cod_instalacion_nueva, jugadores)
        reserva["Codigo"] = reserva_id  # ID de la reserva a mover
        payload = {"reserva": reserva}

        url = f"{BASE_URL}/Reservas/json/moverreserva"
        print(f"[moverreserva] PUT {url} - reservaId={reserva_id} nueva_hora={hora_nueva} instalacion={cod_instalacion_nueva}")
        r = requests.put(url, headers={**HEADERS, "Content-Type": "application/json"},
                         json=payload, timeout=15)
        print(f"[moverreserva] HTTP {r.status_code} - {r.text[:300]}")
        data = r.json()
        result = data.get("MoverReservaResult", {})
        if result.get("StatusOK"):
            print(f"[moverreserva] ✅ {result.get('Mensaje','')}")
            return {"ok": True}
        else:
            msg = result.get("Mensaje") or "Error moviendo reserva"
            print(f"[moverreserva] ❌ {msg}")
            return {"ok": False, "error": msg}
    except Exception as e:
        import traceback; traceback.print_exc()
        return {"ok": False, "error": str(e)}


def marcar_cambio_pagado(reserva_id):
    """Marca el cambio como pagado tras PagarReserva."""
    try:
        url = f"{BASE_URL}/Reservas/json/marcarcambiopagado/{reserva_id},{PROCEDENCIA},{IDIOMA}"
        r = requests.get(url, headers=HEADERS, timeout=10)
        data = r.json()
        return data.get("MarcarCambioPagadoResult", {}).get("StatusOK", False)
    except Exception as e:
        print(f"[marcarcambiopagado] Error: {e}")
        return False


def get_reservas(jugador_id):
    """
    Devuelve las reservas futuras activas del jugador consultando la API del club en tiempo real.
    Siempre fresco — si el socio cancela desde la app del club, desaparece aquí también.
    """
    import re, datetime as dt
    try:
        url = f"{BASE_URL}/Jugadores/json/reservasall/{CENTRO},{jugador_id},{PROCEDENCIA}"
        print(f"[reservasall] GET {url}")
        r = requests.get(url, headers=HEADERS, timeout=10)
        print(f"[reservasall] HTTP {r.status_code}")
        print(f"[reservasall] Raw: {r.text[:800]}")
        data = r.json()
        print(f"[reservasall] Keys: {list(data.keys())}")

        # La clave puede variar — buscarla
        reservas_raw = data.get("ReservasAllResult") or data.get("Reservas") or []
        if isinstance(reservas_raw, dict):
            # A veces viene envuelto
            for v in reservas_raw.values():
                if isinstance(v, list):
                    reservas_raw = v
                    break

        print(f"[reservasall] {len(reservas_raw)} reservas encontradas")
        if reservas_raw:
            print(f"[reservasall] Ejemplo: {str(reservas_raw[0])[:300]}")

        ahora = dt.datetime.now()
        resultado = []

        for res in reservas_raw:
            if not isinstance(res, dict):
                continue

            # Fecha/hora — puede estar en distintos campos
            hora_raw = (
                res.get("fecha_hora_uso") or res.get("fecha_hora") or
                res.get("FechaHora") or res.get("Fecha") or ""
            )
            hora_str = unix_date_to_hhmm(hora_raw) if hora_raw else ""

            # Extraer fecha
            fecha_str_val = ""
            m = re.search(r"/Date\((\d+)", hora_raw)
            if m:
                ts = int(m.group(1)) // 1000
                fecha_dt = dt.datetime.utcfromtimestamp(ts) + dt.timedelta(hours=2)
                fecha_str_val = fecha_dt.strftime("%Y-%m-%d")
                # Solo reservas futuras (a partir de hoy)
                if fecha_dt.date() < ahora.date():
                    continue

            # Instalación — viene como objeto anidado
            inst_obj = res.get("instalacion")
            if isinstance(inst_obj, dict):
                cod_inst = inst_obj.get("codigo")
                descs = inst_obj.get("descripciones") or []
                descripcion = descs[0].get("Value", "").strip() if descs else ""
            else:
                cod_inst = res.get("cod_instalacion") or res.get("CodInstalacion")
                descripcion = res.get("descripcion") or res.get("Descripcion") or ""

            # Jugadores
            jugadores_raw = res.get("jugadores") or res.get("Jugadores") or []
            jugadores_ids = [j.get("codigo") or j.get("Codigo") for j in jugadores_raw if isinstance(j, dict)]

            resultado.append({
                "id": res.get("codigo") or res.get("Codigo") or res.get("IDReserva"),
                "fecha": fecha_str_val,
                "hora": hora_str,
                "cod_instalacion": cod_inst,
                "descripcion": descripcion,
                "jugadores": jugadores_ids,
            })

        # Ordenar por fecha+hora
        resultado.sort(key=lambda x: (x["fecha"], x["hora"]))
        print(f"[reservasall] {len(resultado)} reservas futuras")
        return resultado
    except Exception as e:
        print(f"[reservasall] Error: {e}")
        import traceback; traceback.print_exc()
        return []


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


def filtrar_por_instalacion(huecos, tipo, preferencia=None):
    """
    Filtra y prioriza huecos según tipo y preferencia.
    tipo puede ser un int (instalación única) o string "11,13" (búsqueda dual).
    Si es dual: busca en ambas instalaciones y devuelve lista ordenada por hora
    priorizando el campo preferido cuando hay coincidencia de hora.
    """
    # Modo dual: tipo es string "11,13"
    if isinstance(tipo, str) and "," in tipo:
        tipos = [int(t) for t in tipo.split(",")]
        # Asegurar que preferencia es int para comparar con tipos
        try:
            pref_int = int(preferencia) if preferencia is not None else None
        except (ValueError, TypeError):
            pref_int = None
        preferido = pref_int if pref_int in tipos else tipos[0]
        no_preferido = [t for t in tipos if t != preferido][0]

        huecos_pref = [h for h in huecos if h["cod_instalacion"] == preferido]
        huecos_nopref = [h for h in huecos if h["cod_instalacion"] == no_preferido]

        print(f"[filtrar] Dual: preferido={preferido}({len(huecos_pref)} huecos) alternativo={no_preferido}({len(huecos_nopref)} huecos)")

        # Prioridad absoluta: si hay huecos en el campo preferido, usar solo esos.
        # Solo caer al alternativo si el preferido no tiene nada en toda la franja.
        if huecos_pref:
            return huecos_pref
        if huecos_nopref:
            print(f"[filtrar] Sin huecos en preferido, usando alternativo {no_preferido}")
            return huecos_nopref
        return []

    # Modo simple: tipo es int
    tipo_int = int(tipo) if isinstance(tipo, str) else tipo
    filtrados = [h for h in huecos if h["cod_instalacion"] == tipo_int]
    if filtrados:
        print(f"[filtrar] {len(filtrados)} huecos para instalación {tipo_int}")
        return filtrados
    print(f"[filtrar] Sin huecos para instalación {tipo_int}")
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
    tipo = params["tipo"]  # puede ser int o string "11,13"
    desde = params["desde"]
    hasta = params["hasta"]
    jugadores = params["jugadores"]
    preferencia = params.get("preferencia")  # instalación preferida en modo dual

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
        huecos = filtrar_por_instalacion(huecos, tipo, preferencia)
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


def mejora_loop(key, params):
    """
    Sniper de mejora: busca un hueco mejor para una reserva existente.
    Si lo encuentra: mueve la reserva, crea ticket y paga.
    """
    def set_state(**kwargs):
        with snipers_lock:
            if key in snipers:
                snipers[key].update(kwargs)

    usuario = params["usuario"]
    clave = params["clave"]
    jugador_id = params["jugadorId"]
    reserva_id = params["reservaId"]
    fecha = params["fecha"]
    hora_actual = params["hora_actual"]          # hora a mejorar (ej: "10:10")
    cod_inst_actual = params["cod_instalacion_actual"]
    tipo = params["tipo"]                        # instalación objetivo (puede ser "11,13")
    desde = params["desde"]
    hasta = params["hasta"]
    jugadores = params["jugadores"]
    preferencia = params.get("preferencia")

    with snipers_lock:
        stop_event = snipers[key]["stop_event"]
        poll_interval = snipers[key]["poll_interval"]

    tipo_nombre = {11: "Norte 18h", 12: "Norte 9h", 13: "Sur 18h", 14: "Sur 9h", 15: "Pares 3"}
    print(f"[Mejora:{reserva_id}] Iniciando. Actual:{hora_actual} Buscando:{desde}-{hasta} tipo:{tipo}")

    while not stop_event.is_set():
        with snipers_lock:
            poll_interval = snipers[key]["poll_interval"]
            snipers[key]["attempts"] += 1
            intento = snipers[key]["attempts"]

        print(f"[Mejora:{reserva_id}] Intento #{intento}")
        set_state(mensaje=f"Buscando mejora sobre {hora_actual}… (cada {poll_interval}s)")

        instalaciones = get_instalaciones_dia(usuario, clave, jugador_id, fecha, tipo)
        if instalaciones is None:
            stop_event.wait(poll_interval)
            continue

        huecos = parsear_huecos(instalaciones, desde, hasta)
        huecos = filtrar_por_instalacion(huecos, tipo, preferencia)

        # Solo considerar huecos MEJORES que el actual (hora más temprana)
        huecos_mejores = [h for h in huecos if h["hora"] < hora_actual]
        print(f"[Mejora:{reserva_id}] Huecos mejores que {hora_actual}: {[h['hora'] for h in huecos_mejores]}")

        if not huecos_mejores:
            set_state(mensaje=f"Sin mejora disponible. Reintentando en {poll_interval}s…")
            stop_event.wait(poll_interval)
            continue

        # ¡Hay mejora! Tomar el más temprano
        hueco = huecos_mejores[0]
        hora_nueva = hueco["hora"]
        cod_inst_nueva = hueco["cod_instalacion"]
        set_state(status="found", mensaje=f"¡Mejora a las {hora_nueva}! Moviendo reserva…")
        print(f"[Mejora:{reserva_id}] Mejora encontrada: {hora_nueva} ({cod_inst_nueva}). Moviendo...")

        # Verificar que sigue siendo modificable
        if not es_modificable(reserva_id):
            set_state(status="error", error="La reserva ya no es modificable")
            break

        # Mover reserva
        res_mover = mover_reserva(reserva_id, jugador_id, fecha, hora_nueva, cod_inst_nueva, jugadores)
        if not res_mover["ok"]:
            set_state(status="searching", mensaje=f"Error moviendo: {res_mover['error']}. Reintentando…")
            stop_event.wait(5)
            continue

        # Ticket del nuevo horario
        ticket_res = crear_ticket(reserva_id, jugador_id)
        if not ticket_res["ok"]:
            set_state(status="searching")
            stop_event.wait(5)
            continue

        # Pagar el cambio
        pago_res = pagar_reserva(reserva_id, jugador_id, fecha, hora_nueva, cod_inst_nueva, jugadores, ticket_res["ticket"])
        if not pago_res["ok"]:
            set_state(status="searching")
            stop_event.wait(5)
            continue

        # Marcar cambio como pagado
        marcar_cambio_pagado(reserva_id)

        # ¡ÉXITO!
        fecha_fmt = datetime.strptime(fecha, "%Y-%m-%d").strftime("%d/%m/%Y")
        set_state(
            status="reserved",
            mensaje=f"¡Reserva mejorada! {hora_actual} → {hora_nueva}",
            reserva={
                "id": reserva_id,
                "fecha": fecha_fmt,
                "hora": hora_nueva,
                "hora_anterior": hora_actual,
                "tipo": cod_inst_nueva,
                "jugadores": jugadores,
            }
        )
        print(f"[Mejora:{reserva_id}] ✅ MEJORADA: {hora_actual} → {hora_nueva} {tipo_nombre.get(cod_inst_nueva, cod_inst_nueva)}")
        send_telegram(
            f"🔄 <b>¡Reserva mejorada!</b>\n\n"
            f"📅 <b>Fecha:</b> {fecha_fmt}\n"
            f"🕐 <b>Antes:</b> {hora_actual} → <b>Ahora:</b> {hora_nueva}\n"
            f"🏌️ <b>Campo:</b> {tipo_nombre.get(cod_inst_nueva, str(cod_inst_nueva))}\n"
            f"🔖 <b>Ref:</b> #{reserva_id}"
        )
        break

    with snipers_lock:
        if key in snipers and snipers[key]["status"] == "searching":
            snipers[key]["status"] = "idle"
            snipers[key]["mensaje"] = "Búsqueda de mejora detenida"
    print(f"[Mejora:{reserva_id}] Finalizado")


# ══════════════════════════════════════════════════════
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
