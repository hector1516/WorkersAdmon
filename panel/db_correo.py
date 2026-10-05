"""
panel/db_correo.py — Cuentas de correo de ECCSA_Mailbox (capa de datos)
=====================================================================
Vive aparte de `panel/db.py` a propósito: esas consultas son de las tablas
compartidas con el HUB (usuarios, sesiones, config), y estas son de las tablas
`HUB_Mailbox*`, que creó la app de Mailbox. Mezclarlas haría que una lectura de
correo pareciera parte del login.

─── LA REGLA DE ESTE MÓDULO: LA CREDENCIAL NUNCA SE LEE DESDE ────────────────

La contraseña IMAP se cifra con `mailbox_worker.crypto.encrypt_secret` antes de
tocarla la base, y se guarda en `CredencialCifrada`. **Ninguna función de acá la
devuelve**: `listar_cuentas()` no la trae, y no existe una función que la traiga.

No es prudencia decorativa. La app de Mailbox tiene su propio login y su propio
panel; este panel es de administración y sus sessions vivían en `HUB_Sessions`. Si
la credencial se pudiera leer desde acá, cualquier futura vista que se agregara
—o un log que se olvidara de enmascarar— la expondría sin que nadie tuviera que
querer exponerla.

El worker es el único que la descifra, y solo porque es el único que abre un
socket IMAP.

`fuente_clave()` se importa del worker para que el panel NO pueda cifrar con una
llave distinta de la que el worker descifra: si fueran dos, todo fallaría al
primer sync con un error que no dice "las llaves no coinciden".
"""

from mailbox_worker.crypto import encrypt_secret as encrypt_credencial

from .db import _execute, _rows


# ── Cuentas ───────────────────────────────────────────────────────────────────

# Estados posibles. `PENDIENTE` es el estado de una cuenta recién dada de alta:
# el worker la valida contra el servidor de correo y la promueve a `ACTIVA`, o la
# deja en `ERROR` con el motivo. Por eso el panel NO es el que decide si una
# cuenta funciona: no tiene las credenciales descifradas, y no debería.
ESTADOS = ("ACTIVA", "PENDIENTE", "ERROR", "PAUSADA")


def listar_cuentas():
    """
    Todas las cuentas, con su último estado y a cuánta gente se le asignó.

    NO trae `CredencialCifrada` a propósito (ver el docstring del módulo). La
    columna existe en la base y no se pide.
    """
    return _rows(
        "SELECT c.Id, c.Alias, c.Email, c.ServidorIMAP, c.PuertoIMAP, "
        "       c.ServidorSMTP, c.PuertoSMTP, c.TipoAuth, c.Estado, c.Ubicacion, "
        "       c.Icono, c.Color, c.UltimoSync, c.UltimoError, c.VentanaDias, "
        "       c.MaxMensajes, "
        "       (SELECT COUNT(*) FROM HUB_MailboxCuentasLinks l "
        "         WHERE l.IdCuenta = c.Id) AS CuantosUsan, "
        "       (SELECT COUNT(*) FROM HUB_MailboxMensajes m "
        "         WHERE m.IdCuenta = c.Id AND m.Visto = 0 "
        "           AND m.Eliminado = 0) AS NoLeidos "
        "FROM HUB_MailboxCuentas c ORDER BY c.Alias, c.Email")


def obtener_cuenta(id_cuenta):
    """Una cuenta por id. Igual que `listar_cuentas`, sin la credencial."""
    filas = _rows(
        "SELECT Id, Alias, Email, ServidorIMAP, PuertoIMAP, ServidorSMTP, "
        "       PuertoSMTP, TipoAuth, Estado, Ubicacion, Icono, Color, "
        "       VentanaDias, MaxMensajes, UltimoSync, UltimoError "
        "FROM HUB_MailboxCuentas WHERE Id = %s", (int(id_cuenta),))
    return filas[0] if filas else None


def crear_cuenta(datos: dict, contrasena: str) -> int:
    """
    Da de alta una cuenta y devuelve su Id.

    La contrasena se cifra acá adentro y el resto del camino no la vuelve a
    tocar. Si `contrasena` viene vacía se guarda la cadena vacía cifrada, no
    `None`: el worker distingue "sin credencial" (que marca ERROR con un mensaje
    claro) de "credencial vacía" (que intentaría un LOGIN en blanco).

    El estado inicial es SIEMPRE `PENDIENTE`, nunca `ACTIVA`: una cuenta dada de
    alta no se ha conectado a su servidor de correo todavía, y declararla activa
    sería mentirle al panel (que la mostraría en verde) y al worker (que la
    sincronizaría 20 veces por hora fallando).
    """
    _execute(
        "INSERT INTO HUB_MailboxCuentas "
        "(Alias, Email, ServidorIMAP, PuertoIMAP, ServidorSMTP, PuertoSMTP, "
        " TipoAuth, CredencialCifrada, Estado, Ubicacion, Icono, Color, "
        " VentanaDias, MaxMensajes) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'PENDIENTE', %s, %s, %s, %s, %s)",
        (datos["alias"], datos["email"], datos["servidor_imap"],
         int(datos["puerto_imap"]), datos["servidor_smtp"], int(datos["puerto_smtp"]),
         datos.get("tipo_auth", "PASSWORD"), encrypt_credencial(contrasena),
         datos.get("ubicacion", ""), datos.get("icono", "📮"),
         datos.get("color", "#FF6B00"), int(datos.get("ventana_dias", 90)),
         int(datos.get("max_mensajes", 5000))))
    filas = _rows("SELECT MAX(Id) AS Id FROM HUB_MailboxCuentas")
    return int(filas[0]["Id"] or 0)


def actualizar_cuenta(id_cuenta: int, datos: dict, contrasena: str = ""):
    """
    Actualiza una cuenta. La contrasena SOLO se toca si viene una nueva.

    Es lo que permite corregir el host o el puerto sin tener que volver a escribir
    la contraseña — y, más importante, sin borrarla por accidente: un UPDATE
    incondicional de `CredencialCifrada` con la cadena vacía dejaría la cuenta sin
    poder sincronizar, y sería la forma más fácil de romper algo sin querer.
    """
    campos, vals = [], []
    simples = {
        "alias": "Alias", "email": "Email", "servidor_imap": "ServidorIMAP",
        "servidor_smtp": "ServidorSMTP", "tipo_auth": "TipoAuth",
        "ubicacion": "Ubicacion", "icono": "Icono", "color": "Color",
    }
    for clave, columna in simples.items():
        if clave in datos and datos[clave] is not None:
            campos.append(f"{columna} = %s")
            vals.append(datos[clave])
    for clave, columna in (("puerto_imap", "PuertoIMAP"), ("puerto_smtp", "PuertoSMTP"),
                          ("ventana_dias", "VentanaDias"), ("max_mensajes", "MaxMensajes")):
        if clave in datos and datos[clave] not in (None, ""):
            campos.append(f"{columna} = %s")
            vals.append(int(datos[clave]))

    if contrasena:
        campos.append("CredencialCifrada = %s")
        vals.append(encrypt_credencial(contrasena))

    # Al cambiar el servidor o la autenticación la cuenta vuelve a PENDIENTE: lo
    # que se acaba de cambiar es justo lo que hay que volver a probar.
    if any(k in datos for k in ("servidor_imap", "servidor_smtp", "tipo_auth",
                                "puerto_imap", "puerto_smtp")) or contrasena:
        campos.append("Estado = 'PENDIENTE'")
        campos.append("UltimoError = NULL")

    if not campos:
        return 0
    vals.append(int(id_cuenta))
    return _execute(f"UPDATE HUB_MailboxCuentas SET {', '.join(campos)} WHERE Id = %s",
                    tuple(vals))


def cambiar_estado(id_cuenta: int, estado: str):
    """Cambia el estado. `PENDIENTE` es el que hace que el worker la valide."""
    if estado not in ESTADOS:
        raise ValueError(f"estado desconocido: {estado}")
    return _execute("UPDATE HUB_MailboxCuentas SET Estado = %s WHERE Id = %s",
                    (estado, int(id_cuenta)))


def borrar_cuenta(id_cuenta: int):
    """
    Borra la cuenta y todo lo suyo.

    Los ON DELETE CASCADE de las migraciones 0048/0051 se llevan los mensajes,
    adjuntos, colas, reglas y asignaciones. Lo que NO se lleva es el cuerpo HTML
    en el volumen compartido ni las imágenes de firma: eso son archivos sueltos y
    nadie los borra por nosotros.

    Lo que el CASCADE no se lleva son los archivos sueltos: el cuerpo HTML en
    `datos_dir/cuerpos/<id_cuenta>/`. Esos quedan en disco sin que nada los borre,
    así que la vista avisa antes de borrar diciendo lo que queda, y el borrado
    físico de esa carpeta lo hace una tarea del worker.
    """
    return _execute("DELETE FROM HUB_MailboxCuentas WHERE Id = %s", (int(id_cuenta),))


# ── Asignación a usuarios ─────────────────────────────────────────────────────

def usuarios_con_cuenta(id_cuenta):
    """Los `IdUsuario` que ven esta cuenta."""
    return [f["IdUsuario"] for f in _rows(
        "SELECT IdUsuario FROM HUB_MailboxCuentasLinks WHERE IdCuenta = %s "
        "ORDER BY IdUsuario", (int(id_cuenta),))]


def asignar(id_cuenta: int, id_usuario: int, asignado: bool):
    """
    Asigna (o desasigna) una cuenta a un usuario.

    El `SELECT` antes del `INSERT` es para no comerse el error de la llave
    única: una asignación repetida desde el panel es una doble clic, no un bug,
    y no debería salir un 500 por eso.
    """
    if asignado:
        ya = _rows("SELECT Id FROM HUB_MailboxCuentasLinks "
                   "WHERE IdCuenta = %s AND IdUsuario = %s",
                   (int(id_cuenta), int(id_usuario)))
        if ya:
            return 0
        return _execute("INSERT INTO HUB_MailboxCuentasLinks (IdCuenta, IdUsuario) "
                        "VALUES (%s, %s)", (int(id_cuenta), int(id_usuario)))
    return _execute("DELETE FROM HUB_MailboxCuentasLinks "
                    "WHERE IdCuenta = %s AND IdUsuario = %s",
                    (int(id_cuenta), int(id_usuario)))


def usuarios_activos():
    """Para el selector de a quién asignar."""
    return _rows("SELECT Id, Nombre, Email FROM HUB_Users WHERE Activo = 1 "
                 "ORDER BY Nombre, Email")