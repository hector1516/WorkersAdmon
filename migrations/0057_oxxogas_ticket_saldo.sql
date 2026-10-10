-- 0057_oxxogas_ticket_saldo.sql
-- El aviso del ticket de OxxoGas ahora muestra el saldo actual del monedero
-- (HUB_Config.govale_saldo, lo llena cron_sync_govale_vouchers.py).
--
-- El productor ya manda el campo {Saldo} (notif_messages.datos_ticket,
-- api/telegram_hub y notif_dispatch): aquí solo se le agrega el placeholder a
-- las plantillas que YA existen en la base, que es donde vive el texto.
--
-- Se respeta lo que el operador haya personalizado: si la plantilla todavía no
-- menciona {Saldo} se le agrega la línea al final; si ya lo trae, no se toca.

IF EXISTS (
    SELECT 1 FROM dbo.HUB_WhatsappEventos
    WHERE IdEvento = N'OXXOGAS_TICKET'
      AND PlantillaMensaje NOT LIKE N'%{Saldo}%'
)
BEGIN
    UPDATE dbo.HUB_WhatsappEventos
    SET PlantillaMensaje = PlantillaMensaje + CHAR(13) + CHAR(10) + N'💰 Saldo OxxoGas: {Saldo}'
    WHERE IdEvento = N'OXXOGAS_TICKET';
END
GO

IF EXISTS (
    SELECT 1 FROM dbo.HUB_TelegramEventos
    WHERE IdEvento = 'OXXOGAS_TICKET'
      AND PlantillaMensaje NOT LIKE N'%{Saldo}%'
)
BEGIN
    UPDATE dbo.HUB_TelegramEventos
    SET PlantillaMensaje = PlantillaMensaje + CHAR(13) + CHAR(10) + N'*Saldo OxxoGas:* {Saldo}'
    WHERE IdEvento = 'OXXOGAS_TICKET';
END
GO
