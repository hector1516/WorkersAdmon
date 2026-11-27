"""
canales.py — Interruptores maestros de los canales de salida
============================================================
Telegram, WhatsApp (OpenWA) y push PWA se pueden apagar desde el panel sin
tocar el código de cada worker. El valor vive en `HUB_Config`; este módulo es
la única puerta que lo lee.

Regla de seguridad: si la base falla, el canal se considera ACTIVO. Es peor
dejar al sistema mudo por un error de lectura que mandar un aviso de más.

El valor solo se escribe cuando alguien usa `guardar` (desde el panel); las
claves no se siembran aquí. Sin fila el canal está activo (default 1).
"""
import time

CANALES = {
    "telegram": "canal_telegram_activo",
    "whatsapp": "canal_whatsapp_activo",
    "push": "canal_push_activo",
}

TTL = 60                     # segundos que se recuerda una lectura
_cache = {}                  # canal -> (monotonic de expiración, bool)
_config_lista = None         # None = todavía no se consultó


def _log(msg):
    print(f"[canales] {msg}", flush=True)


def _db_configurada():
    """True si hay credenciales de BD. Sin ellas no se intenta conectar.

    En pruebas y en un equipo sin `secretos_local.py` no hay password: intentar
    igual costaría los 30 s de reintentos de `eccsa_db.get_connection` para
    terminar en el default. Ese default es justo lo que se devuelve.
    """
    global _config_lista
    if _config_lista is None:
        try:
            import config_db
            cfg = config_db.load_db_config() or {}
            _config_lista = bool(str(cfg.get("password") or "").strip())
        except Exception as exc:
            _log(f"no se pudo leer la config de BD: {exc}")
            _config_lista = False
    return _config_lista


def _conexion():
    """Conexión de la capa de datos de los workers (perezosa: evita el ciclo)."""
    import eccsa_db
    return eccsa_db.get_connection()


def _normalizar(valor):
    """Un valor ausente o vacío deja el canal prendido."""
    return str(valor if valor is not None else "1").strip().lower() not in (
        "0", "false", "no", "off", "apagado")


def _leer(canal):
    """Lee el interruptor de HUB_Config. Lanza si la BD falla."""
    if not _db_configurada():
        return True
    clave = CANALES[canal]
    with _conexion() as conn:
        with conn.cursor(as_dict=True) as cur:
            cur.execute("SELECT Valor FROM HUB_Config WHERE Clave = %s",
                        (clave,))
            fila = cur.fetchone()
    if not fila:
        return True
    return _normalizar(fila.get("Valor"))


def activo(canal):
    """True si el canal puede mandar. Default True ante cualquier fallo."""
    if canal not in CANALES:
        _log(f"canal desconocido: {canal}")
        return True

    ahora = time.monotonic()
    guardado = _cache.get(canal)
    if guardado and guardado[0] > ahora:
        return guardado[1]

    try:
        valor = _leer(canal)
    except Exception as exc:
        _log(f"error leyendo {canal}; queda activo: {exc}")
        valor = True

    _cache[canal] = (ahora + TTL, valor)
    return valor


def estado():
    """{"telegram": bool, "whatsapp": bool, "push": bool}."""
    return {canal: activo(canal) for canal in CANALES}


def guardar(canal, valor):
    """Guarda el interruptor en HUB_Config y refresca la caché.

    Lanza si la BD falla: la pantalla necesita saber que no se guardó.
    """
    if canal not in CANALES:
        raise ValueError(f"canal desconocido: {canal}")

    clave = CANALES[canal]
    numero = "1" if _normalizar(valor) and bool(valor) else "0"
    with _conexion() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                MERGE HUB_Config AS target
                USING (SELECT %s AS Clave) AS source
                ON target.Clave = source.Clave
                WHEN MATCHED THEN
                    UPDATE SET Valor = %s, Actualizado = GETDATE()
                WHEN NOT MATCHED THEN
                    INSERT (Clave, Valor) VALUES (%s, %s);
            """, (clave, numero, clave, numero))
            conn.commit()

    _cache[canal] = (time.monotonic() + TTL, numero == "1")
