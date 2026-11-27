-- 0074 · ECCSita: claves de la crónica semanal con IA
--
-- Qué cambia:
--   1. `wa_grupo_ia` (default 1): la crónica de la semana usa la IA si hay
--      ApiKey + modelo en HUB_AIConfig. Con 0 se queda la determinista.
--   2. `wa_grupo_semana_dia` (default 1): día ISO (1=lunes … 7=domingo) del
--      worker `wa_grupo_semana`.
--   3. `wa_grupo_semana_hora` (default 09:00): hora de México en que abre la
--      ventana de 30 minutos.
--
-- Idempotente: las claves solo se insertan si faltan, para no pisar un valor
-- ya cambiado desde el panel.
IF NOT EXISTS (SELECT 1 FROM dbo.HUB_Config WHERE Clave = 'wa_grupo_ia')
    INSERT INTO dbo.HUB_Config (Clave, Valor) VALUES ('wa_grupo_ia', '1');
GO

IF NOT EXISTS (SELECT 1 FROM dbo.HUB_Config WHERE Clave = 'wa_grupo_semana_dia')
    INSERT INTO dbo.HUB_Config (Clave, Valor) VALUES ('wa_grupo_semana_dia', '1');
GO

IF NOT EXISTS (SELECT 1 FROM dbo.HUB_Config WHERE Clave = 'wa_grupo_semana_hora')
    INSERT INTO dbo.HUB_Config (Clave, Valor) VALUES ('wa_grupo_semana_hora', '09:00');
GO

PRINT '0074_eccsita_semana_ia.sql aplicada'
GO
