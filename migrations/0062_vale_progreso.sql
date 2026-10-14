-- 0062_vale_progreso.sql
-- Progreso de la generación del vale. Lo escribe `vales_worker`
-- (oxxogas_vales_automation.crear_vale) y lo pinta Field como barra de espera.
--
--   0        = en cola
--   1..99    = generándose (PasoVale dice en qué va)
--   100      = listo (con folio)
--
-- Son NULL por defecto: las filas viejas y las que no se han intentado quedan
-- como "en cola".

IF COL_LENGTH('dbo.HUB_SolicitudVales', 'Progreso') IS NULL
    ALTER TABLE dbo.HUB_SolicitudVales ADD Progreso TINYINT NULL;
GO

IF COL_LENGTH('dbo.HUB_SolicitudVales', 'PasoVale') IS NULL
    ALTER TABLE dbo.HUB_SolicitudVales ADD PasoVale NVARCHAR(80) NULL;
GO

IF COL_LENGTH('dbo.HUB_SolicitudVales', 'ProgresoFecha') IS NULL
    ALTER TABLE dbo.HUB_SolicitudVales ADD ProgresoFecha DATETIME NULL;
GO
