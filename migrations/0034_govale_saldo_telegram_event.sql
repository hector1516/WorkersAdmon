-- 0034_govale_saldo_telegram_event.sql
-- Agregar evento GOVALE_SALDO para notificación diaria de saldo Go Vale por Telegram
IF OBJECT_ID('dbo.HUB_TelegramEventos', 'U') IS NOT NULL
BEGIN
    IF NOT EXISTS (SELECT 1 FROM dbo.HUB_TelegramEventos WHERE IdEvento = 'GOVALE_SALDO')
    BEGIN
        INSERT INTO dbo.HUB_TelegramEventos (IdEvento, Nombre, PlantillaMensaje, AdjuntarArchivo, Activo)
        VALUES (
            'GOVALE_SALDO',
            N'Saldo Go Vale Diario',
            N'💰 *Saldo Go Vale* — ${Saldo}\nEstado: {Estado}\nUmbral alerta: ${Umbral}\nÚltima revisión: {UltimaRevision}',
            0, 1
        );
    END
END
GO