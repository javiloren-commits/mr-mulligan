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
from zoneinfo import ZoneInfo

TZ_MADRID = ZoneInfo("Europe/Madrid")
from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

# ══════════════════════════════════════════════════════
#  CONFIGURACIÓN
# ══════════════════════════════════════════════════════
# API nueva de la app del club (desde nov-2026): REST + token Bearer
API_BASE = os.environ.get("API_BASE", "https://reservas.rshecc.es/WSAppsStartMaster.1.0.0/api")
# API antigua: solo como plan B para mover/anular mientras no capturemos esos endpoints nuevos
API_VERSION = os.environ.get("API_VERSION", "7.1.0")
LEGACY_BASE = f"https://reservas.rshecc.es/AppWebServices.{API_VERSION}"
LEGACY_AUTH = "Basic d2Vic2VydmljZTpTZWd1cmlkYWQqd2ViMTY="
CENTRO = "24"
PROCEDENCIA = "6"
IDIOMA = "1"
DEPORTE_GOLF = "6"  # ID fijo del deporte golf en la API

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID = os.environ.get("CHAT_ID", "")

# Modalidades (campo "tipo" de la API): 7 = 18 hoyos, 31 = 9 hoyos
MODALIDAD_18 = 7
MODALIDAD_9 = 31
# Salidas (campo "codigo"/"Instalacion"): 11 Hoyo 1 Norte, 13 Hoyo 1 Sur, 14 Hoyo 10 Sur, 15 Pares 3
SALIDAS = {11: "Norte (hoyo 1)", 13: "Sur (hoyo 1)", 14: "Sur (hoyo 10)", 15: "Pares 3"}
JUGADORES_EXCLUIDOS = {45474, 50550}  # nunca usar como sustitutos

HEADERS = {
    "User-Agent": "StartMasterRSHECC/1 CFNetwork/3896.200.61 Darwin/27.2.0",
    "Accept": "application/json",
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

    asegurar_sesion(body)
    try:
        contactos = get_contactos(jugador_id)
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
    asegurar_sesion(body)
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
    fecha = body.get("fecha") or body.get("key")  # key = "mejora_{id}" para búsquedas de mejora
    with snipers_lock:
        if fecha:
            targets = [fecha] if fecha in snipers else []
        else:
            targets = list(snipers.keys())
        for f in targets:
            snipers[f]["stop_event"].set()
            snipers[f]["status"] = "idle"
            snipers[f]["mensaje"] = "Busqueda cancelada"
    return jsonify({"ok": True, "detenidos": targets})


def ahora_madrid():
    return datetime.now(TZ_MADRID)


def parse_net_date(raw):
    """'/Date(1778104800000+0200)/' -> datetime en hora de Madrid."""
    import re
    m = re.search(r"/Date\((\d+)", str(raw))
    if not m:
        return None
    return datetime.fromtimestamp(int(m.group(1)) / 1000, TZ_MADRID)


def get_fechas_abiertas(jugador_id=None):
    """Fechas (YYYY-MM-DD) que el club ya permite reservar. None si falla la consulta."""
    try:
        r = api("GET", "Reservas/diaspermitidos", jugador_id,
                params={"deporte": DEPORTE_GOLF, "procedencia": PROCEDENCIA}, timeout=10)
        dias = r.json()
        if not isinstance(dias, list):
            print(f"[fechas] Respuesta inesperada: {str(dias)[:200]}")
            return None
        fechas = []
        for d in dias:  # "2026-11-21T00:00:00" (o /Date()/ en la API antigua)
            s = str(d)
            if len(s) >= 10 and s[4] == "-":
                fechas.append(s[:10])
            else:
                f = parse_net_date(s)
                if f:
                    fechas.append(f.strftime("%Y-%m-%d"))
        return sorted(set(fechas))
    except Exception as e:
        print(f"[fechas] Error: {e}")
        return None


def calcular_inicio_verificacion(fecha, inicio_param=None):
    """
    Cuándo empezar a comprobar si el día ya está abierto (hora de Madrid).
    - inicio_param explícito ('YYYY-MM-DDTHH:MM') si viene del frontend.
    - Sábado/domingo: abren el jueves de esa misma semana -> jueves a las 05:00.
    - Resto de días: ahora.
    """
    ahora = ahora_madrid()
    if inicio_param:
        try:
            ini = datetime.fromisoformat(inicio_param)
            ini = ini.replace(tzinfo=TZ_MADRID) if ini.tzinfo is None else ini.astimezone(TZ_MADRID)
            return max(ini, ahora)
        except ValueError:
            pass
    d = datetime.strptime(fecha, "%Y-%m-%d")
    if d.weekday() in (5, 6):
        jueves = d - timedelta(days=d.weekday() - 3)
        ini = jueves.replace(hour=5, minute=0, tzinfo=TZ_MADRID)
        return max(ini, ahora)
    return ahora


APERTURAS_FILE = os.environ.get("APERTURAS_FILE", "aperturas.log")
aperturas = []  # también en memoria para consultarlas vía /aperturas


def registrar_apertura(fecha, detectada, ultima_cerrada):
    """Guarda cuándo se detectó que un día abrió para reservas (para ajustar la hora de comprobación)."""
    linea = {
        "fecha_juego": fecha,
        "detectado_abierto": detectada.strftime("%Y-%m-%d %H:%M:%S"),
        "ultima_vez_cerrado": ultima_cerrada.strftime("%Y-%m-%d %H:%M:%S") if ultima_cerrada else None,
    }
    aperturas.append(linea)
    print(f"[APERTURA] {json.dumps(linea)}")
    try:
        with open(APERTURAS_FILE, "a") as f:
            f.write(json.dumps(linea) + "\n")
    except Exception as e:
        print(f"[APERTURA] No se pudo escribir el fichero: {e}")
    rango = f"entre las {ultima_cerrada:%H:%M:%S} y las {detectada:%H:%M:%S}" if ultima_cerrada else f"antes de las {detectada:%H:%M:%S}"
    send_telegram(f"🔓 <b>Reservas abiertas</b> para el {datetime.strptime(fecha, '%Y-%m-%d'):%d/%m}\nAbrieron {rango} ({detectada:%d/%m}). Buscando hora…")


@app.route("/aperturas", methods=["GET"])
def aperturas_endpoint():
    """Historial de horas de apertura detectadas (memoria + fichero)."""
    registros = list(aperturas)
    try:
        with open(APERTURAS_FILE) as f:
            de_fichero = [json.loads(l) for l in f if l.strip()]
        registros = de_fichero + [r for r in registros if r not in de_fichero]
    except FileNotFoundError:
        pass
    return jsonify({"ok": True, "aperturas": registros})


@app.route("/fechas", methods=["POST"])
def fechas_endpoint():
    """Devuelve las fechas disponibles para reservar."""
    body = request.get_json(silent=True) or {}
    asegurar_sesion(body)
    fechas = get_fechas_abiertas(body.get("jugadorId"))
    if fechas is None:
        return jsonify({"ok": False, "fechas": []})
    print(f"[fechas] {len(fechas)} fechas disponibles: {fechas[:5]}")
    return jsonify({"ok": True, "fechas": fechas})


@app.route("/validar_jugadores", methods=["POST"])
def validar_jugadores_endpoint():
    """Verifica qué jugadores ya tienen reserva en una fecha concreta."""
    body = request.get_json()
    jugador_id = body.get("jugadorId")
    fecha = body.get("fecha", "")
    ids = body.get("ids", [])
    asegurar_sesion(body)

    ocupados = [jid for jid in ids if not jugador_puede_jugar(jugador_id, fecha, jid)]
    print(f"[validar_jugadores] fecha={fecha} ocupados={ocupados}")
    return jsonify({"ok": True, "ocupados": ocupados})


@app.route("/reservas", methods=["POST"])
def reservas_endpoint():
    """Devuelve las reservas actuales del jugador."""
    body = request.get_json()
    jugador_id = body.get("jugadorId")
    asegurar_sesion(body)
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
    asegurar_sesion(body)
    poll_interval = int(body.get("pollInterval", 60))
    poll_interval = max(10, min(300, poll_interval))

    # Verificar que la reserva es modificable
    if not es_modificable(reserva_id, body["jugadorId"]):
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

    asegurar_sesion(body)
    jugador_id = body.get("jugadorId")
    try:
        res = anular_reserva(reserva_id, jugador_id)
        return jsonify({"ok": True} if res["ok"] else {"ok": False, "error": res["error"]})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


# ══════════════════════════════════════════════════════
#  LÓGICA DE LA API DEL CLUB (WSAppsStartMaster 1.0.0, nov-2026)
#  REST + JSON con token Bearer (JWT, ~1 h) que se obtiene con /Jugadores/login
# ══════════════════════════════════════════════════════

_sesiones = {}            # jugador_id(str) -> {"usuario", "clave", "token", "exp"}
_sesiones_lock = threading.Lock()


def _jwt_exp(token):
    """Lee el campo exp del JWT (sin verificar firma)."""
    try:
        import base64
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return int(json.loads(base64.urlsafe_b64decode(payload)).get("exp", 0))
    except Exception:
        return 0


def _login_raw(usuario, clave):
    """POST /Jugadores/login. Devuelve el JSON de respuesta."""
    body = {"Usuario": usuario, "Centro": int(CENTRO), "Procedencia": int(PROCEDENCIA),
            "Idioma": int(IDIOMA), "Clave": clave}
    url = f"{API_BASE}/Jugadores/login"
    headers = {**HEADERS, "Content-Type": "application/json;charset=UTF-8"}
    r = requests.post(url, json=body, headers=headers, timeout=15)
    if r.status_code in (401, 403):
        # por si el login también exige cabecera: probar con la Basic de la app antigua
        r = requests.post(url, json=body, headers={**headers, "Authorization": LEGACY_AUTH}, timeout=15)
    print(f"[login] HTTP {r.status_code}")
    return r.json()


def registrar_sesion(usuario, clave):
    """Hace login, guarda token+credenciales y devuelve el resultado normalizado."""
    data = _login_raw(usuario, clave)
    if not data.get("StatusOK") or not data.get("Token"):
        return {"ok": False, "error": data.get("Mensaje") or "Credenciales incorrectas"}
    token = data["Token"]
    jugador = data.get("Jugador") or {}
    jugador_id = jugador.get("codigo")
    exp = _jwt_exp(token) or int(time.time()) + 3000
    with _sesiones_lock:
        _sesiones[str(jugador_id)] = {"usuario": usuario, "clave": clave, "token": token, "exp": exp}
    nombre = " ".join(p for p in [jugador.get("nombre", ""), jugador.get("apellido", "")] if p).strip() or usuario
    return {"ok": True, "jugadorId": jugador_id, "nombre": nombre}


def asegurar_sesion(body):
    """Guarda credenciales que llegan del frontend (start/mover/anular) si aún no hay sesión."""
    jid = str(body.get("jugadorId") or "")
    usuario, clave = body.get("usuario"), body.get("clave")
    if not usuario or not clave:
        return
    with _sesiones_lock:
        s = _sesiones.get(jid)
        if s and s["usuario"] == usuario and s["clave"] == clave:
            return
    try:
        registrar_sesion(usuario, clave)
    except Exception as e:
        print(f"[sesion] Error registrando sesión: {e}")


def get_token(jugador_id=None, forzar=False):
    """Token válido para el jugador (renueva con las credenciales guardadas si caduca)."""
    with _sesiones_lock:
        if jugador_id is not None and str(jugador_id) in _sesiones:
            s = dict(_sesiones[str(jugador_id)])
        elif _sesiones:
            s = dict(next(iter(_sesiones.values())))
        else:
            return None
    if forzar or s["exp"] - 300 < time.time():
        print(f"[sesion] Renovando token de {s['usuario'][-4:]}")
        res = registrar_sesion(s["usuario"], s["clave"])
        if not res["ok"]:
            print(f"[sesion] No se pudo renovar: {res['error']}")
            return None
        with _sesiones_lock:
            return _sesiones[str(res["jugadorId"])]["token"]
    return s["token"]


def api(method, path, jugador_id=None, params=None, body=None, timeout=15):
    """Llamada autenticada a la API nueva. Reintenta una vez renovando el token si da 401."""
    url = f"{API_BASE}/{path}"
    for intento in range(2):
        token = get_token(jugador_id, forzar=(intento == 1))
        headers = dict(HEADERS)
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if body is not None:
            headers["Content-Type"] = "application/json;charset=UTF-8"
        r = requests.request(method, url, headers=headers, params=params, json=body, timeout=timeout)
        if r.status_code != 401:
            return r
        print(f"[api] 401 en {path}, renovando token")
    return r


def fecha_iso(fecha, hora="00:00"):
    """'2026-11-21', '08:10' -> '2026-11-21T08:10:00'"""
    return f"{fecha}T{hora}:00"


def do_login(usuario, clave):
    """Login real contra la API del club."""
    try:
        res = registrar_sesion(usuario, clave)
        if res["ok"]:
            print(f"[login] OK - jugadorId={res['jugadorId']}")
        return res
    except Exception as e:
        return {"ok": False, "error": f"Error de conexión: {str(e)}"}


def get_contactos(jugador_id):
    """Lista [{id, nombre}] de contactos del jugador."""
    r = api("GET", "Jugadores/partidoscontactos", jugador_id,
            params={"deporte": DEPORTE_GOLF, "idioma": IDIOMA}, timeout=10)
    valor = r.json().get("Valor") or {}
    if isinstance(valor, str):
        valor = json.loads(valor) if valor else {}
    contactos = []
    for j in valor.get("Contactos", []) or []:
        cid, nombre = j.get("Codigo"), j.get("Nombre") or ""
        if cid and nombre:
            contactos.append({"id": cid, "nombre": nombre, "hcp": None})
    return contactos


def jugador_puede_jugar(jugador_id, fecha, jid):
    """validarjugador: StatusOK=false => ya tiene reserva ese día / no puede."""
    try:
        r = api("GET", "Reservas/validarjugador", jugador_id, timeout=8, params={
            "deporte": DEPORTE_GOLF, "fecha": fecha_iso(fecha), "idioma": IDIOMA,
            "jugador": jid, "procedencia": PROCEDENCIA})
        return bool(r.json().get("StatusOK", True))
    except Exception:
        return True  # si falla, asumir disponible


def obtener_jugadores_disponibles(jugador_id, fecha, jugadores_deseados):
    """
    Comprueba cada jugador deseado; si alguno no puede jugar ese día lo sustituye
    por un contacto libre (nunca los de JUGADORES_EXCLUIDOS). El titular se mantiene.
    """
    disponibles, faltan = [], 0
    for jid in jugadores_deseados:
        if str(jid) == str(jugador_id) or jugador_puede_jugar(jugador_id, fecha, jid):
            disponibles.append(jid)
        else:
            print(f"[jugadores] {jid} no disponible el {fecha}, buscando sustituto")
            faltan += 1
    if not faltan:
        return jugadores_deseados
    try:
        contactos = get_contactos(jugador_id)
    except Exception as e:
        print(f"[jugadores] Error leyendo contactos: {e}")
        contactos = []
    for c in contactos:
        if faltan == 0:
            break
        cid = c["id"]
        if cid in JUGADORES_EXCLUIDOS or cid in jugadores_deseados or cid in disponibles:
            continue
        if jugador_puede_jugar(jugador_id, fecha, cid):
            print(f"[jugadores] Sustituto: {cid} ({c['nombre']})")
            disponibles.append(cid)
            faltan -= 1
    if faltan:
        print(f"[jugadores] Solo {len(disponibles)}/{len(jugadores_deseados)} jugadores disponibles")
    return disponibles or [jugadores_deseados[0]]


def get_instalaciones_dia(usuario, clave, jugador_id, fecha, modalidad=MODALIDAD_18, num_jugadores=1):
    """
    Huecos libres del día para una modalidad (7 = 18 hoyos, 31 = 9 hoyos).
    Una sola llamada devuelve todas las salidas (11 Norte h1, 13 Sur h1, 14 Sur h10, 15 Pares 3).
    """
    try:
        r = api("GET", "Reservas/instalacionesdia", jugador_id, params={
            "bloques": 5, "deporte": DEPORTE_GOLF, "fecha": fecha_iso(fecha), "idioma": IDIOMA,
            "numjugadores": max(1, int(num_jugadores)), "procedencia": PROCEDENCIA, "tipo": modalidad})
        print(f"[instalacionesdia] {fecha} modalidad={modalidad} HTTP {r.status_code}")
        if r.status_code != 200:
            print(f"[instalacionesdia] Error: {r.text[:200]}")
            return None
        data = r.json()
        if not isinstance(data, list):
            print(f"[instalacionesdia] Respuesta inesperada: {str(data)[:200]}")
            return None
        return {"InstalacionesDiaResult": data}
    except Exception as e:
        print(f"[instalacionesdia] Error: {e}")
        return None


def fecha_str(fecha, hora):
    """Construye FechaStr: YYYYMMDDHHMM"""
    return fecha.replace("-", "") + hora.replace(":", "")


def pago_a_cuenta(jugador_id):
    return [{
        "Centro": int(CENTRO), "Idioma": int(IDIOMA), "Procedencia": int(PROCEDENCIA),
        "TransaccionTpv": {}, "Tipo": 8, "LineaTicket": 0, "Jugador": int(jugador_id),
        "Importe": 0, "ASaldo": 0,
        "FormaPago": {"EsTarjeta": False, "EsAbonoAutorizado": False, "Codigo": -2,  # A CUENTA
                      "EsMonedero": False, "EsAbono": False, "EsDeuda": False},
    }]


def build_reserva_base(jugador_id, fecha, hora, cod_instalacion, jugadores, modalidad=MODALIDAD_18,
                       desmarcar_cobro=False, cod_jugador_paga=0, tipo_cobro=0, pago=None, codigo=0):
    """Objeto reserva común a crearreserva y pagarreserva (formato de la captura nov-2026)."""
    return {
        "Centro": int(CENTRO),
        "DesmarcarCobro": desmarcar_cobro,
        "TipoJugador": 1,
        "Deporte": int(DEPORTE_GOLF),
        "JugadorFamiliar": 0,
        "CodJugadorProcesar": 0,
        "CodJugadorPaga": int(cod_jugador_paga),
        "TipoCobro": tipo_cobro,
        "Codigo": int(codigo),
        "FechaStr": fecha_str(fecha, hora),
        "Instalacion": int(cod_instalacion),
        "Idioma": int(IDIOMA),
        "Pago": pago or [],
        "Componentes": [{"Codigo": int(j)} for j in jugadores],
        "BuscarJugadores": False,
        "Tipo": int(modalidad),
        "TiempoJuego": 5,
        "Procedencia": int(PROCEDENCIA),
    }


def _resultado(r, etiqueta, defecto):
    print(f"[{etiqueta}] HTTP {r.status_code} - {r.text[:300]}")
    try:
        data = r.json()
    except Exception:
        return {"ok": False, "error": f"HTTP {r.status_code}"}
    if isinstance(data, dict) and data.get("StatusOK"):
        return {"ok": True, "data": data}
    msg = (data.get("Mensaje") if isinstance(data, dict) else None) or defecto
    return {"ok": False, "error": msg}


def crear_reserva(usuario, clave, jugador_id, fecha, hora, cod_instalacion, jugadores, modalidad=MODALIDAD_18):
    """POST /Reservas/crearreserva -> Valor = id de la reserva (queda sin pagar hasta pagarreserva)."""
    try:
        payload = build_reserva_base(jugador_id, fecha, hora, cod_instalacion, jugadores, modalidad)
        print(f"[crear_reserva] {payload['FechaStr']} inst={cod_instalacion} tipo={modalidad} jugadores={jugadores}")
        res = _resultado(api("POST", "Reservas/crearreserva", jugador_id, body=payload), "crear_reserva",
                         "Error creando reserva")
        if res["ok"]:
            return {"ok": True, "reservaId": res["data"].get("Valor")}
        return res
    except Exception as e:
        import traceback; traceback.print_exc()
        return {"ok": False, "error": str(e)}


def crear_ticket(reserva_id, jugador_id):
    """POST /Reservas/crearticket?codpeticion=..."""
    try:
        r = api("POST", "Reservas/crearticket", jugador_id, params={
            "codpeticion": reserva_id, "idioma": IDIOMA, "jugador": jugador_id,
            "procedencia": PROCEDENCIA, "tipocobro": 1, "tiposolicitud": 0})
        res = _resultado(r, "crear_ticket", "Error creando ticket")
        return {"ok": True, "ticket": res["data"]} if res["ok"] else res
    except Exception as e:
        return {"ok": False, "error": str(e)}


def pagar_reserva(reserva_id, jugador_id, fecha, hora, cod_instalacion, jugadores, ticket_data, modalidad=MODALIDAD_18):
    """POST /Reservas/pagarreserva — FormaPago -2 = A CUENTA (domiciliación)."""
    try:
        payload = build_reserva_base(
            jugador_id, fecha, hora, cod_instalacion, jugadores, modalidad,
            desmarcar_cobro=True, cod_jugador_paga=jugador_id,
            tipo_cobro=(ticket_data or {}).get("TipoCobro", 1),
            pago=pago_a_cuenta(jugador_id), codigo=reserva_id)
        res = _resultado(api("POST", "Reservas/pagarreserva", jugador_id, body=payload), "pagar_reserva",
                         "Error en el pago")
        return {"ok": True} if res["ok"] else res
    except Exception as e:
        import traceback; traceback.print_exc()
        return {"ok": False, "error": str(e)}


def borrar_reserva_no_pagada(reserva_id, jugador_id):
    """DELETE /Reservas/borrarreservanopago — limpia una reserva creada que no se llegó a pagar."""
    try:
        r = api("DELETE", "Reservas/borrarreservanopago", jugador_id,
                params={"codigo": reserva_id, "idioma": IDIOMA}, timeout=10)
        return _resultado(r, "borrar_no_pagada", "No se pudo borrar")["ok"]
    except Exception as e:
        print(f"[borrar_no_pagada] Error: {e}")
        return False


def es_modificable(reserva_id, jugador_id=None):
    try:
        r = api("GET", "Reservas/esmodificable", jugador_id,
                params={"codigo": reserva_id, "procedencia": PROCEDENCIA}, timeout=10)
        return r.json() is True
    except Exception as e:
        print(f"[esmodificable] Error: {e}")
        return False


def es_anulable(reserva_id, jugador_id=None):
    try:
        r = api("GET", "Reservas/esanulable", jugador_id,
                params={"codigo": reserva_id, "procedencia": PROCEDENCIA, "tipo": 1}, timeout=10)
        return r.json() is True
    except Exception as e:
        print(f"[esanulable] Error: {e}")
        return False


def _legacy(method, path, body=None):
    """Llamada a la API antigua (AppWebServices) — solo como plan B para mover/anular."""
    url = f"{LEGACY_BASE}/{path}"
    h = {**HEADERS, "Authorization": LEGACY_AUTH}
    if body is not None:
        h["Content-Type"] = "application/json"
    return requests.request(method, url, headers=h, json=body, timeout=15)


def _primer_ok(intentos, etiqueta, defecto):
    """Prueba varias llamadas candidatas (endpoints no capturados aún) hasta que una responda OK."""
    ultimo = {"ok": False, "error": defecto}
    for desc, fn in intentos:
        try:
            r = fn()
            if r.status_code == 404:
                print(f"[{etiqueta}] {desc}: 404")
                continue
            res = _resultado(r, f"{etiqueta}:{desc}", defecto)
            if res["ok"]:
                return res
            ultimo = res
        except Exception as e:
            print(f"[{etiqueta}] {desc}: {e}")
    return ultimo


def mover_reserva(reserva_id, jugador_id, fecha, hora_nueva, cod_instalacion_nueva, jugadores, modalidad=MODALIDAD_18):
    """
    Mueve una reserva a otro horario/salida.
    OJO: el endpoint de la API nueva no está capturado todavía; se prueba el nombre esperado
    y, si no existe, la API antigua.
    """
    reserva = build_reserva_base(jugador_id, fecha, hora_nueva, cod_instalacion_nueva, jugadores, modalidad,
                                 codigo=reserva_id)
    res = _primer_ok([
        ("nueva PUT", lambda: api("PUT", "Reservas/moverreserva", jugador_id, body=reserva)),
        ("nueva POST", lambda: api("POST", "Reservas/moverreserva", jugador_id, body=reserva)),
        ("antigua", lambda: _legacy("PUT", "Reservas/json/moverreserva", {"reserva": reserva})),
    ], "moverreserva", "Error moviendo reserva")
    # La API antigua envuelve la respuesta en MoverReservaResult
    if res["ok"] and "MoverReservaResult" in res["data"]:
        inner = res["data"]["MoverReservaResult"]
        if not inner.get("StatusOK"):
            return {"ok": False, "error": inner.get("Mensaje") or "Error moviendo reserva"}
    return {"ok": True} if res["ok"] else res


def marcar_cambio_pagado(reserva_id, jugador_id=None):
    res = _primer_ok([
        ("nueva", lambda: api("GET", "Reservas/marcarcambiopagado", jugador_id,
                              params={"codigo": reserva_id, "procedencia": PROCEDENCIA, "idioma": IDIOMA})),
        ("antigua", lambda: _legacy("GET", f"Reservas/json/marcarcambiopagado/{reserva_id},{PROCEDENCIA},{IDIOMA}")),
    ], "marcarcambiopagado", "No se pudo marcar")
    return res["ok"]


def anular_reserva(reserva_id, jugador_id=None):
    """
    Anula una reserva ya pagada. El endpoint nuevo no está capturado todavía: se prueban
    los nombres esperados y, por último, la API antigua.
    """
    q = {"codigo": reserva_id, "idioma": IDIOMA, "procedencia": PROCEDENCIA}
    res = _primer_ok([
        ("DELETE anularreserva", lambda: api("DELETE", "Reservas/anularreserva", jugador_id, params=q)),
        ("POST anularreserva", lambda: api("POST", "Reservas/anularreserva", jugador_id, params=q)),
        ("PUT anularreserva", lambda: api("PUT", "Reservas/anularreserva", jugador_id, params=q)),
        ("antigua", lambda: _legacy("GET", f"Reservas/json/AnularReserva/{reserva_id},{PROCEDENCIA},{IDIOMA}")),
    ], "anular", "No se pudo anular")
    if res["ok"] and "AnularReservaResult" in res["data"]:
        inner = res["data"]["AnularReservaResult"]
        if not inner.get("StatusOK"):
            return {"ok": False, "error": inner.get("Mensaje") or "No se pudo anular"}
    return {"ok": True} if res["ok"] else res


def get_reservas(jugador_id):
    """Reservas futuras del jugador (GET /Jugadores/reservasall)."""
    try:
        r = api("GET", "Jugadores/reservasall", jugador_id,
                params={"idioma": IDIOMA, "jugador": jugador_id, "procedencia": PROCEDENCIA}, timeout=10)
        print(f"[reservasall] HTTP {r.status_code}")
        data = r.json()
        if not isinstance(data, list):
            print(f"[reservasall] Respuesta inesperada: {str(data)[:300]}")
            return []
        hoy = ahora_madrid().strftime("%Y-%m-%d")
        resultado = []
        for res in data:
            if not isinstance(res, dict):
                continue
            fecha_raw = res.get("Fecha") or ""          # "2026-11-21T08:10:00"
            fecha_val, hora_val = fecha_raw[:10], fecha_raw[11:16]
            if not fecha_val and res.get("FechaStr"):
                fs = res["FechaStr"]
                fecha_val, hora_val = f"{fs[:4]}-{fs[4:6]}-{fs[6:8]}", f"{fs[8:10]}:{fs[10:12]}"
            if fecha_val and fecha_val < hoy:
                continue
            resultado.append({
                "id": res.get("Codigo"),
                "fecha": fecha_val,
                "hora": hora_val,
                "cod_instalacion": res.get("Instalacion"),
                "descripcion": (res.get("DescInstalacion") or "").strip(),
                "modalidad": res.get("Tipo") or MODALIDAD_18,
                "tarifa": res.get("TipoTarifa") or "",
                "jugadores": [c.get("Codigo") for c in res.get("Componentes") or [] if isinstance(c, dict)],
                "nombres": [c.get("Nombre") for c in res.get("Componentes") or [] if isinstance(c, dict)],
                "localizador": res.get("Localizador"),
            })
        resultado.sort(key=lambda x: (x["fecha"], x["hora"]))
        print(f"[reservasall] {len(resultado)} reservas futuras")
        return resultado
    except Exception as e:
        print(f"[reservasall] Error: {e}")
        import traceback; traceback.print_exc()
        return []


def hora_hhmm(raw):
    """'2026-11-21T08:10:00' -> '08:10'; también acepta el formato antiguo /Date(ms+0100)/."""
    s = str(raw or "")
    if "T" in s and len(s) >= 16:
        return s[11:16]
    return unix_date_to_hhmm(s)


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

            hora_str = hora_hhmm(hora_raw)
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


def seleccionar_hueco(huecos_pref, huecos_alt, franja, modo_hora):
    """
    Selecciona el mejor hueco según modo_hora y preferencia de campo.
    franja: (desde_min, hasta_min) en minutos
    modo_hora: "pronto" | "mitad" | "tarde" | "pronto_mitad" | "mitad_tarde"
    Lógica:
      - Calcular zona objetivo dentro de la franja
      - Buscar en zona: primero preferido, luego alternativo
      - Si no hay nada en zona: buscar en toda la franja
    """
    desde_min, hasta_min = franja
    rango = hasta_min - desde_min

    # Definir zona objetivo según modo
    if modo_hora == "pronto":
        zona_inicio = desde_min
        zona_fin = desde_min + rango // 3
        elegir = "primero"   # más temprano
    elif modo_hora == "mitad":
        zona_inicio = desde_min + rango // 3
        zona_fin = desde_min + 2 * rango // 3
        elegir = "centro"
    elif modo_hora == "tarde":
        zona_inicio = desde_min + 2 * rango // 3
        zona_fin = hasta_min
        elegir = "ultimo"  # más tarde posible
    elif modo_hora == "pronto_mitad":
        zona_inicio = desde_min
        zona_fin = desde_min + 2 * rango // 3
        elegir = "centro"
    elif modo_hora == "mitad_tarde":
        zona_inicio = desde_min + rango // 3
        zona_fin = hasta_min
        elegir = "centro"
    else:
        # Sin preferencia de horario: más temprano del preferido
        zona_inicio = desde_min
        zona_fin = hasta_min
        elegir = "primero"

    centro_zona = (zona_inicio + zona_fin) // 2
    print(f"[seleccionar] modo={modo_hora} zona={zona_inicio}-{zona_fin} centro={centro_zona} elegir={elegir}")

    def en_zona(h):
        m = time_to_minutes(h["hora"])
        return zona_inicio <= m <= zona_fin

    def score(h):
        m = time_to_minutes(h["hora"])
        if elegir == "primero":
            return m           # menor = mejor (más temprano)
        elif elegir == "ultimo":
            return -m          # mayor = mejor (más tarde)
        else:
            return abs(m - centro_zona)  # más cercano al centro = mejor

    # 1. Preferido en zona
    candidatos = [h for h in huecos_pref if en_zona(h)]
    if candidatos:
        elegido = min(candidatos, key=score)
        print(f"[seleccionar] Preferido en zona: {elegido['hora']} ({elegido['descripcion'].strip()})")
        return elegido

    # 2. Preferido en toda la franja (la salida preferida tiene prioridad absoluta)
    if huecos_pref:
        elegido = min(huecos_pref, key=score)
        print(f"[seleccionar] Preferido fuera de zona: {elegido['hora']}")
        return elegido

    # 3. Alternativo en zona
    candidatos = [h for h in huecos_alt if en_zona(h)]
    if candidatos:
        elegido = min(candidatos, key=score)
        print(f"[seleccionar] Alternativo en zona: {elegido['hora']} ({elegido['descripcion'].strip()})")
        return elegido

    # 4. Alternativo en toda la franja
    if huecos_alt:
        elegido = min(huecos_alt, key=score)
        print(f"[seleccionar] Alternativo fuera de zona: {elegido['hora']}")
        return elegido

    return None


def parse_salidas(tipo):
    """'11' -> [11]; '11,13' -> [11, 13]; 11 -> [11]"""
    if isinstance(tipo, (list, tuple)):
        return [int(t) for t in tipo]
    return [int(t) for t in str(tipo).split(",") if str(t).strip()]


def normalizar_modalidad(params):
    """
    Modalidad (7 = 18 hoyos, 31 = 9 hoyos) y salidas pedidas.
    Compatibilidad: el tipo antiguo 12 ('Norte 9 hoyos') pasa a 9 hoyos por el Norte.
    """
    salidas = parse_salidas(params.get("tipo"))
    modalidad = int(params.get("modalidad") or MODALIDAD_18)
    if salidas == [12]:
        salidas, modalidad = [11], MODALIDAD_9
    if modalidad == MODALIDAD_9:
        salidas = [s for s in salidas if s == 11] or [11]  # 9 hoyos: solo hoyo 1 del Norte
    return modalidad, salidas


def nombre_salida(cod, modalidad=MODALIDAD_18):
    hoyos = "9h" if int(modalidad or MODALIDAD_18) == MODALIDAD_9 else "18h"
    try:
        return f"{SALIDAS.get(int(cod), str(cod))} · {hoyos}"
    except (TypeError, ValueError):
        return str(cod)


def filtrar_por_instalacion(huecos, tipo, preferencia=None, modo_hora=None, desde=None, hasta=None):
    """
    Filtra los huecos a las salidas pedidas y elige el mejor.
    - Una salida: el mejor según modo_hora.
    - Varias ('11,13', '11,13,14'): la preferida tiene prioridad absoluta; el resto es alternativa.
    Devuelve [hueco] (o la lista ordenada si no hay modo_hora).
    """
    salidas = parse_salidas(tipo)
    try:
        pref_int = int(preferencia) if preferencia not in (None, "") else None
    except (ValueError, TypeError):
        pref_int = None
    preferido = pref_int if pref_int in salidas else salidas[0]
    alternativas = [s for s in salidas if s != preferido]

    huecos_pref = [h for h in huecos if h["cod_instalacion"] == preferido]
    huecos_alt = sorted([h for h in huecos if h["cod_instalacion"] in alternativas],
                        key=lambda h: (h["hora"], alternativas.index(h["cod_instalacion"])))
    print(f"[filtrar] preferido={preferido}({len(huecos_pref)}) alt={alternativas}({len(huecos_alt)}) modo={modo_hora}")

    if modo_hora and desde and hasta:
        franja = (time_to_minutes(desde), time_to_minutes(hasta))
        elegido = seleccionar_hueco(huecos_pref, huecos_alt, franja, modo_hora)
        return [elegido] if elegido else []
    return huecos_pref or huecos_alt


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
    modo_hora = params.get("modo_hora")      # pronto | mitad | tarde | pronto_mitad | mitad_tarde

    with snipers_lock:
        stop_event = snipers[fecha]["stop_event"]
        poll_interval = snipers[fecha]["poll_interval"]

    modalidad, salidas = normalizar_modalidad(params)
    tipo = ",".join(str(s) for s in salidas)
    print(f"[Sniper:{fecha}] Iniciando. Tipo:{tipo} {desde}-{hasta} Jugadores:{jugadores} Intervalo:{poll_interval}s")

    # ── Fase de espera: el día puede no estar abierto todavía ──
    DIAS = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]
    inicio = calcular_inicio_verificacion(fecha, params.get("inicio"))
    ultima_cerrada = None
    while not stop_event.is_set():
        ahora = ahora_madrid()
        if ahora < inicio:
            set_state(mensaje=f"Programada: verificará apertura desde el {DIAS[inicio.weekday()]} {inicio:%d/%m} a las {inicio:%H:%M}")
            stop_event.wait(min((inicio - ahora).total_seconds(), 300))
            continue
        abiertas = get_fechas_abiertas(jugador_id)
        if abiertas is None or fecha in abiertas:
            if abiertas is not None:
                print(f"[Sniper:{fecha}] Día abierto para reservas")
                if ultima_cerrada:  # solo si lo vimos cerrado antes: así sabemos cuándo abrió
                    registrar_apertura(fecha, ahora, ultima_cerrada)
            break  # abierto (o no se pudo comprobar): pasar a buscar hora
        # Ventana de apertura (07:55-08:15): comprobar cada 10s para no llegar tarde
        en_ventana = ahora.replace(hour=7, minute=55, second=0) <= ahora <= ahora.replace(hour=8, minute=15, second=0)
        ultima_cerrada = ahora
        espera = 10 if en_ventana else poll_interval
        set_state(mensaje=f"Día aún no abierto. Comprobando cada {espera}s…")
        print(f"[Sniper:{fecha}] Aún no abierto ({ahora:%H:%M:%S}), reintento en {espera}s")
        stop_event.wait(espera)

    while not stop_event.is_set():
        with snipers_lock:
            poll_interval = snipers[fecha]["poll_interval"]
            snipers[fecha]["attempts"] += 1
            intento = snipers[fecha]["attempts"]

        print(f"[Sniper:{fecha}] Intento #{intento}")
        set_state(mensaje=f"Buscando huecos en {desde}–{hasta}… (cada {poll_interval}s)")

        instalaciones = get_instalaciones_dia(usuario, clave, jugador_id, fecha, modalidad, len(jugadores))
        if instalaciones is None:
            set_state(mensaje="Error consultando disponibilidad")
            stop_event.wait(poll_interval)
            continue

        huecos = parsear_huecos(instalaciones, desde, hasta)
        huecos = filtrar_por_instalacion(huecos, tipo, preferencia, modo_hora, desde, hasta)
        print(f"[Sniper:{fecha}] Huecos tipo {tipo}: {[h['hora'] for h in huecos]}")

        if not huecos:
            set_state(mensaje=f"Sin huecos. Reintentando en {poll_interval}s…")
            stop_event.wait(poll_interval)
            continue

        hueco = huecos[0]
        hora = hueco["hora"]
        cod_instalacion = hueco["cod_instalacion"]
        set_state(status="found", mensaje=f"¡Hueco a las {hora}! Verificando jugadores…")
        print(f"[Sniper:{fecha}] Hueco a las {hora} (instalación {cod_instalacion}). Verificando jugadores...")

        # Verificar disponibilidad y sustituir jugadores si hace falta
        jugadores_finales = obtener_jugadores_disponibles(jugador_id, fecha, jugadores)
        if jugadores_finales != jugadores:
            set_state(mensaje=f"¡Hueco a las {hora}! Ajustando jugadores no disponibles…")

        res_crear = crear_reserva(usuario, clave, jugador_id, fecha, hora, cod_instalacion, jugadores_finales, modalidad)
        if not res_crear["ok"]:
            print(f"[Sniper:{fecha}] Error reserva: {res_crear['error']}")
            set_state(status="searching", mensaje=f"Error: {res_crear['error']}. Reintentando…")
            stop_event.wait(5)
            continue

        reserva_id = res_crear["reservaId"]
        ticket_res = crear_ticket(reserva_id, jugador_id)
        pago_res = {"ok": False, "error": ticket_res.get("error")}
        if ticket_res["ok"]:
            pago_res = pagar_reserva(reserva_id, jugador_id, fecha, hora, cod_instalacion, jugadores_finales,
                                     ticket_res["ticket"], modalidad)
        if not pago_res["ok"]:
            # No dejar la reserva colgada sin pagar: borrarla y volver a intentarlo
            print(f"[Sniper:{fecha}] Pago fallido ({pago_res.get('error')}), borrando reserva #{reserva_id}")
            borrar_reserva_no_pagada(reserva_id, jugador_id)
            set_state(status="searching", mensaje=f"Error en el pago: {pago_res.get('error')}. Reintentando…")
            stop_event.wait(5)
            continue

        fecha_fmt = datetime.strptime(fecha, "%Y-%m-%d").strftime("%d/%m/%Y")
        set_state(
            status="reserved",
            mensaje="¡Reserva confirmada!",
            reserva={"id": reserva_id, "fecha": fecha_fmt, "hora": hora, "tipo": cod_instalacion,
                     "modalidad": modalidad, "jugadores": jugadores_finales},
        )
        print(f"[Sniper:{fecha}] ✅ CONFIRMADA: {fecha_fmt} {hora} {nombre_salida(cod_instalacion, modalidad)}")
        send_telegram(
            f"⛳ <b>¡Reserva confirmada!</b>\n\n"
            f"📅 <b>Fecha:</b> {fecha_fmt}\n"
            f"🕐 <b>Hora:</b> {hora}\n"
            f"🏌️ <b>Salida:</b> {nombre_salida(cod_instalacion, modalidad)}\n"
            f"👥 <b>Jugadores:</b> {len(jugadores_finales)}\n"
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
    modo_hora = params.get("modo_hora")

    with snipers_lock:
        stop_event = snipers[key]["stop_event"]
        poll_interval = snipers[key]["poll_interval"]

    modalidad, salidas = normalizar_modalidad(params)
    tipo = ",".join(str(s) for s in salidas)
    print(f"[Mejora:{reserva_id}] Iniciando. Actual:{hora_actual} Buscando:{desde}-{hasta} tipo:{tipo} modo={modo_hora}")

    while not stop_event.is_set():
        with snipers_lock:
            poll_interval = snipers[key]["poll_interval"]
            snipers[key]["attempts"] += 1
            intento = snipers[key]["attempts"]

        print(f"[Mejora:{reserva_id}] Intento #{intento}")
        set_state(mensaje=f"Buscando mejora sobre {hora_actual}… (cada {poll_interval}s)")

        instalaciones = get_instalaciones_dia(usuario, clave, jugador_id, fecha, modalidad, len(jugadores))
        if instalaciones is None:
            stop_event.wait(poll_interval)
            continue

        huecos = parsear_huecos(instalaciones, desde, hasta)
        huecos = [h for h in huecos if h["cod_instalacion"] in salidas]
        # Un hueco es "mejor" si:
        # 1. Es más temprano que la hora actual, O
        # 2. Es la misma hora pero en el campo preferido (y el actual no lo es)
        try:
            pref_int = int(preferencia) if preferencia else None
        except (ValueError, TypeError):
            pref_int = None

        def es_mejora(h):
            if h["hora"] < hora_actual:
                return True
            if h["hora"] == hora_actual and pref_int and h["cod_instalacion"] == pref_int and h["cod_instalacion"] != cod_inst_actual:
                return True
            return False

        huecos_mejores_raw = [h for h in huecos if es_mejora(h)]
        print(f"[Mejora:{reserva_id}] Huecos mejores que {hora_actual} inst={cod_inst_actual}: {[(h['hora'], h['cod_instalacion']) for h in huecos_mejores_raw]}")

        if not huecos_mejores_raw:
            set_state(mensaje=f"Sin mejora disponible. Reintentando en {poll_interval}s…")
            stop_event.wait(poll_interval)
            continue

        # Aplicar preferencia de campo y horario sobre los candidatos
        horas_mejores = [h["hora"] for h in huecos_mejores_raw]
        desde_real = min(horas_mejores)
        huecos_seleccion = filtrar_por_instalacion(huecos_mejores_raw, tipo, preferencia, modo_hora, desde_real, hora_actual)

        if not huecos_seleccion:
            set_state(mensaje=f"Sin mejora disponible. Reintentando en {poll_interval}s…")
            stop_event.wait(poll_interval)
            continue

        hueco = huecos_seleccion[0]
        hora_nueva = hueco["hora"]
        cod_inst_nueva = hueco["cod_instalacion"]
        set_state(status="found", mensaje=f"¡Mejora a las {hora_nueva}! Moviendo reserva…")
        print(f"[Mejora:{reserva_id}] Mejora encontrada: {hora_nueva} ({cod_inst_nueva}). Moviendo...")

        # Verificar que sigue siendo modificable
        if not es_modificable(reserva_id, jugador_id):
            set_state(status="error", error="La reserva ya no es modificable")
            break

        # Mover reserva
        res_mover = mover_reserva(reserva_id, jugador_id, fecha, hora_nueva, cod_inst_nueva, jugadores, modalidad)
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
        pago_res = pagar_reserva(reserva_id, jugador_id, fecha, hora_nueva, cod_inst_nueva, jugadores,
                                 ticket_res["ticket"], modalidad)
        if not pago_res["ok"]:
            set_state(status="searching")
            stop_event.wait(5)
            continue

        # Marcar cambio como pagado
        marcar_cambio_pagado(reserva_id, jugador_id)

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
                "modalidad": modalidad,
                "jugadores": jugadores,
            }
        )
        print(f"[Mejora:{reserva_id}] ✅ MEJORADA: {hora_actual} → {hora_nueva} {nombre_salida(cod_inst_nueva, modalidad)}")
        send_telegram(
            f"🔄 <b>¡Reserva mejorada!</b>\n\n"
            f"📅 <b>Fecha:</b> {fecha_fmt}\n"
            f"🕐 <b>Antes:</b> {hora_actual} → <b>Ahora:</b> {hora_nueva}\n"
            f"🏌️ <b>Salida:</b> {nombre_salida(cod_inst_nueva, modalidad)}\n"
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
