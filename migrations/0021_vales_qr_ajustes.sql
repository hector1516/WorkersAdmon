-- 0021_vales_qr_ajustes.sql
-- Ajustes al módulo Vale QR: agregar IdAutomovil, IdCliente, quitar Cantidad/MontoUnit,
-- validación 1 vale por automóvil por día.

-- 1) Agregar columnas de relación
IF COL_LENGTH('dbo.HUB_SolicitudVales', 'IdAutomovil') IS NULL
    ALTER TABLE dbo.HUB_SolicitudVales ADD IdAutomovil INT NULL;
GO
IF COL_LENGTH('dbo.HUB_SolicitudVales', 'IdCliente') IS NULL
    ALTER TABLE dbo.HUB_SolicitudVales ADD IdCliente VARCHAR(10) NULL;
GO

-- 2) Default de MontoUnit a 500 (ya existía, solo por si acaso)
-- La columna Cantidad y MontoUnit se mantienen por compatibilidad pero se ignora en la UI

-- 3) Índice para la validación de 1 vale por automóvil por día
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_SolicitudVales_AutoDia' AND object_id = OBJECT_ID('dbo.HUB_SolicitudVales'))
    CREATE INDEX IX_SolicitudVales_AutoDia ON dbo.HUB_SolicitudVales(IdAutomovil, FechaSolicitud) WHERE IdAutomovil IS NOT NULL;
GO
