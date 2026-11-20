# Configuración de gunicorn (se carga sola desde la raíz del repo con `gunicorn sniper:app`).
# Las búsquedas viven en memoria del proceso: debe haber UN solo worker.
# Con hilos, una llamada lenta al club no bloquea al resto de usuarios, y el timeout
# amplio evita que gunicorn mate el worker (y con él todas las búsquedas) a mitad de una reserva.
workers = 1
worker_class = "gthread"
threads = 8
timeout = 180
graceful_timeout = 30
