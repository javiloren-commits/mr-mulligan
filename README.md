# Mr. Mulligan – RSHECC Golf Auto-Booking

Sistema automático de reservas de tee times para la Real Sociedad Hípica Española Club de Campo.

## Estructura

```
mr-mulligan/
├── index.html        # Frontend (Netlify)
├── sniper.py         # Backend Flask (Render.com)
├── requirements.txt
└── README.md
```

## Despliegue

### Backend (Render.com)

1. Crea un nuevo servicio **Web Service** en Render.com
2. Conecta el repositorio Git
3. Configura:
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `gunicorn sniper:app`
   - **Environment:** Python 3.11
4. Añade las variables de entorno:
   - `TELEGRAM_TOKEN` → tu token del bot de Telegram
   - `CHAT_ID` → tu chat ID de Telegram

### Frontend (Netlify)

1. Sube `index.html` a Netlify (drag & drop en netlify.com)
2. **IMPORTANTE:** Edita la línea del archivo `index.html`:
   ```js
   const BACKEND_URL = 'https://TU-APP.onrender.com';
   ```
   Cambia `TU-APP` por el nombre que Render.com asigne a tu servicio.

## API del Club – Flujo documentado

```
1. GET  /Jugadores/json/login?centro=24&usuario=XXX&clave=YYY&procedencia=6&idioma=1
2. GET  /Jugadores/json/partidoscontactos/24,{jugadorId},6,1
3. GET  /Reservas/json/instalacionesdia/{jugadorId},{fechaHora},24,{tipo},6,1,4,1,4
4. PUT  /Reservas/json/CrearReserva   (body JSON)
5. GET  /Reservas/json/crearticket/{reservaId},0,{jugadorId},1,6,1
6. PUT  /Reservas/json/PagarReserva   (body JSON con ticket)
7. GET  /Reservas/json/AnularReserva/{reservaId},6,1
```

## Variables importantes (de la captura Charles)

- `centro = 24` (RSHECC)
- `procedencia = 6` (app móvil)
- `idioma = 1` (español)
- Tipos de campo: `6`=Norte 18h, `21`=Norte 9h, `1`=Sur 18h, `2`=Sur 9h, `8`=Pares 3

## Notas técnicas

- El endpoint `instalacionesdia` devuelve todos los huecos del día. El sniper filtra por franja horaria y selecciona el más temprano disponible.
- El sniper hace polling cada 30 segundos (configurable con `POLL_INTERVAL`).
- Las notificaciones Telegram se envían con el bot configurado en las variables de entorno.
- **IMPORTANTE:** Los parámetros exactos de `CrearReserva` y `PagarReserva` pueden necesitar ajuste fino según la respuesta real de la API. Captura una sesión exitosa con Charles para verificar el body exacto.
