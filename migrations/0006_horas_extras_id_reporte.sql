-- 0006_horas_extras_id_reporte.sql
-- Migración: agrega columna IdReporte a HUB_HorasExtrasRegistros para vincular
-- los registros de horas extras generados automáticamente desde ReportesServicio.

IF OBJECT_ID('dbo.HUB_HorasExtrasRegistros', 'U') IS NOT NULL
   AND COL_LENGTH('dbo.HUB_HorasExtrasRegistros', 'IdReporte') IS NULL
BEGIN
    ALTER TABLE dbo.HUB_HorasExtrasRegistros ADD IdReporte INT NULL;
END
GO
