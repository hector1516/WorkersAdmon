-- 0032_oxxogas_ticket_orden_columnas.sql
-- Reordena la plantilla de Telegram OXXOGAS_TICKET al mismo orden que la
-- tabla "Tickets Físicos Capturados": Fecha, Nombre, Cantidad, Folio, Auto, Cliente, Descripción.
-- Solo se actualiza si la plantilla aún es la semilla original de 0018
-- (no pisa personalizaciones hechas desde la UI de Telegram).

IF EXISTS (
    SELECT 1 FROM dbo.HUB_TelegramEventos
    WHERE IdEvento = 'OXXOGAS_TICKET'
      AND PlantillaMensaje = N'*Ticket #{FolioTicket}* — {Automovil} — {Cliente}'
)
BEGIN
    UPDATE dbo.HUB_TelegramEventos
    SET PlantillaMensaje = N'*Fecha:* {Fecha} {Hora}
*Nombre:* {Nombre}
*Cantidad:* {Cantidad}
*Folio ticket:* {Folio}
*Auto:* {Auto}
*Cliente:* {Cliente}
*Descripción:* {Descripcion}'
    WHERE IdEvento = 'OXXOGAS_TICKET';
END
GO
