-- Migración 0025: Agregar campo Kilometros a HUB_SolicitudVales
-- Al solicitar un vale QR se registrarán los kilómetros actuales del vehículo
-- para tener un control completo de odómetro junto con cada carga de gasolina.

IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('HUB_SolicitudVales') AND name = 'Kilometros')
BEGIN
    ALTER TABLE HUB_SolicitudVales ADD Kilometros INT NULL;
    PRINT 'Columna Kilometros agregada a HUB_SolicitudVales.';
END
ELSE
    PRINT 'Columna Kilometros ya existe en HUB_SolicitudVales.';
GO
