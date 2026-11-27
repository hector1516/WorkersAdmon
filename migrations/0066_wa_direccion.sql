-- 0066 - Direccion de los mensajes de WhatsApp
--
-- La bandeja (fase 4) necesita pintar el historial de un chat en orden, y para
-- eso hay que distinguir lo que entra de lo que sale. Hasta ahora
-- `HUB_WhatsappEntrante` solo guardaba lo que llegaba.
--
--   IN  = mensaje del contacto (lo escribe el webhook)
--   OUT = respuesta del bot o de una persona desde el panel
--
-- Idempotente: se puede ejecutar mas de una vez.
IF COL_LENGTH('dbo.HUB_WhatsappEntrante', 'Direccion') IS NULL
BEGIN
    ALTER TABLE dbo.HUB_WhatsappEntrante
        ADD Direccion VARCHAR(3) NOT NULL
            CONSTRAINT DF_HUB_WhatsappEntrante_Direccion DEFAULT 'IN';
END
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'IX_HUB_WhatsappEntrante_Chat'
      AND object_id = OBJECT_ID('dbo.HUB_WhatsappEntrante')
)
BEGIN
    CREATE INDEX IX_HUB_WhatsappEntrante_Chat
        ON dbo.HUB_WhatsappEntrante (ChatId, Fecha);
END
GO
