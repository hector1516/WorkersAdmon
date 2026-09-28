"""Lógica compartida entre vales de gasolina y tickets de OxxoGas.

Un vale y el ticket que se registra al cargar son el MISMO evento capturado
dos veces. Antes cada parte vivía por su lado y no había forma de saber qué
vale cerraba qué ticket, así que se escribían vehículo, cliente y
descripción dos veces.

Este módulo concentra las tres reglas para que las dos rutas de alta
(POST /tickets y el sync offline en routers/sync.py) hagan exactamente lo
mismo. La regla que más importa: el vale NUNCA es obligatorio. A veces el vale
no llega y el combustible se carga manual desde la página de OxxoGas, así
que un ticket sin vale es un registro válido.

Estados del vale:  PENDIENTE -> APROBADO -> GENERADO (tiene QR) -> CERRADO
"Abierto" = APROBADO o GENERADO y sin ticket. Como los vales nunca expiran,
esa lista solo se acota porque se van cerrando.
"""

ESTADOS_ABIERTOS = ("APROBADO", "GENERADO")


def folio_ya_registrado(cur, folio: str, excluir_id=None):
    """Devuelve el ticket que ya usa ese folio, o None.

    El folio es la identidad real de una carga: la misma foto, mismo
    vehículo y casi la misma hora. Sin esta validación el folio 324745780
    quedó dos veces registradas y nada lo impedía.
    """
    if not folio:
        return None
    sql = ("SELECT Id, FolioTicket, IdVehiculo, FechaRegistro, Estacion "
           "FROM HUB_OxxoGasTickets WHERE FolioTicket = %s")
    args = [folio]
    if excluir_id is not None:
        sql += " AND Id <> %s"
        args.append(excluir_id)
    cur.execute(sql, tuple(args))
    return cur.fetchone()


def vales_abiertos(cur, id_vehiculo: int, id_cliente: str = None):
    """Vales sin gastar de un vehículo, del más reciente al más viejo.

    Se usa al registrar un ticket: si hay uno solo se preselecciona y se
    prellena el formulario; si hay varios se listan para que el usuario diga
    cuál gastó. Devuelve también la antigüedad en días para poder resaltar
    los viejos: como los vales nunca expiran, uno de hace meses sigue
    apareciendo como "disponible" y es fácil gastarlo por error.
    """
    sql = """
        SELECT s.Id, s.IdAutomovil, s.FechaSolicitud, s.Estatus, s.Descripcion,
               s.Kilometros, s.MontoUnit, s.Cantidad, s.Placa, s.IdCliente,
               a.MarcaModelo AS Vehiculo, c.Cliente AS Empresa,
               DATEDIFF(DAY, s.FechaSolicitud, GETDATE()) AS DiasAbierto
        FROM HUB_SolicitudVales s
        LEFT JOIN HUB_Automoviles a ON s.IdAutomovil = a.Id
        LEFT JOIN clientes c ON s.IdCliente = c.IdCliente
        WHERE s.IdAutomovil = %s
          AND s.IdTicketCierre IS NULL
          AND s.Estatus IN %s
    """
    args = [id_vehiculo, ESTADOS_ABIERTOS]
    if id_cliente:
        sql += " AND s.IdCliente = %s"
        args.append(id_cliente)
    sql += " ORDER BY s.FechaSolicitud DESC"
    cur.execute(sql, tuple(args))
    return cur.fetchall()


def cerrar_vale(cur, vale_id: int, ticket_id: int) -> bool:
    """Marca el vale como gastado por un ticket. Devuelve False si ya estaba.

    IdTicketCierre es lo que permite aplicar la regla de "solo se borran
    vales sin ticket" con una lectura simple, sin joins.

    Los callers usan cursor as_dict=True, así que se lee por clave y no por
    posición.
    """
    if not vale_id:
        return False
    cur.execute("SELECT IdTicketCierre FROM HUB_SolicitudVales WHERE Id = %s", (vale_id,))
    fila = cur.fetchone()
    if not fila:
        return False
    ya_cerrado = fila["IdTicketCierre"] if isinstance(fila, dict) else fila[0]
    if ya_cerrado is not None:
        return False
    cur.execute("""
        UPDATE HUB_SolicitudVales
        SET IdTicketCierre = %s, FechaCierre = GETDATE(), Estatus = 'CERRADO'
        WHERE Id = %s AND IdTicketCierre IS NULL
    """, (ticket_id, vale_id))
    return cur.rowcount > 0


def reabrir_vale(cur, vale_id: int) -> bool:
    """Deshace el cierre. Para cuando se vincula mal o se borra un ticket.

    Vuelve a GENERADO si tenía QR; si no la tenía, a APROBADO para que el
    worker de Go Vale la vuelva a generar.
    """
    if not vale_id:
        return False
    cur.execute("""
        UPDATE HUB_SolicitudVales
        SET IdTicketCierre = NULL, FechaCierre = NULL,
            Estatus = CASE WHEN (CodigoQR IS NULL OR LTRIM(RTRIM(CodigoQR)) = '')
                           THEN 'APROBADO' ELSE 'GENERADO' END
        WHERE Id = %s AND IdTicketCierre IS NOT NULL
    """, (vale_id,))
    return cur.rowcount > 0


def vale_con_ticket(cur, vale_id: int):
    """Vale con los datos de su ticket de cierre, para pintar la lista."""
    cur.execute("""
        SELECT s.Id, s.IdTicketCierre, s.FechaCierre, s.Estatus,
               t.FolioTicket, t.Estacion, t.FechaRegistro
        FROM HUB_SolicitudVales s
        LEFT JOIN HUB_OxxoGasTickets t ON t.Id = s.IdTicketCierre
        WHERE s.Id = %s
    """, (vale_id,))
    return cur.fetchone()
