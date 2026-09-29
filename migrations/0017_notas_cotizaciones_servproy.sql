-- 0017_notas_cotizaciones_servproy.sql
-- Migración: AGREGA columna Notas al índice de cotizaciones de Servicios y Proyectos
-- (replica la columna Notas del módulo antiguo CotizacionesReportes que no se conservó)

IF COL_LENGTH('dbo.Indice_CotizacionesServProy', 'Notas') IS NULL
BEGIN
    ALTER TABLE dbo.Indice_CotizacionesServProy
        ADD Notas NVARCHAR(MAX) NULL;
END
GO