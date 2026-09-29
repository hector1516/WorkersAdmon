-- 0007_horas_extras_calculo_precision.sql
-- Migración: amplía la precisión de HorasExtrasCalculadas en HUB_HorasExtrasRegistros
-- para soportar reportes de servicio largos (varios días) sin overflow numérico.

IF OBJECT_ID('dbo.HUB_HorasExtrasRegistros', 'U') IS NOT NULL
BEGIN
    ALTER TABLE dbo.HUB_HorasExtrasRegistros ALTER COLUMN HorasExtrasCalculadas DECIMAL(10,2) NOT NULL;
END
GO
