-- Migración 0033: Agregar columna Estacion a tickets y tabla de historial de estaciones
-- Fecha: 2026-09-23
-- Descripción: 
--   1. Agrega columna Estacion a HUB_OxxoGasTickets (NOT NULL con default vacío para tickets existentes)
--   2. Crea tabla HUB_EstacionesTickets para historial compartido global

-- 1. Columna Estacion en tickets (antes de FolioTicket lógicamente, pero en BD es ADD COLUMN)
IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE Name = N'Estacion' AND Object_ID = Object_ID(N'HUB_OxxoGasTickets'))
BEGIN
    ALTER TABLE HUB_OxxoGasTickets ADD Estacion NVARCHAR(200) NULL;
    -- Actualizar tickets existentes con string vacío para que NOT NULL funcione si se desea
    UPDATE HUB_OxxoGasTickets SET Estacion = '' WHERE Estacion IS NULL;
    ALTER TABLE HUB_OxxoGasTickets ALTER COLUMN Estacion NVARCHAR(200) NOT NULL;
END

-- 2. Tabla de historial de estaciones (catálogo global, tipo contactos)
IF NOT EXISTS (SELECT 1 FROM sysobjects WHERE name = 'HUB_EstacionesTickets' AND type = 'U')
BEGIN
    CREATE TABLE dbo.HUB_EstacionesTickets (
        IdEstacion INT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        Estacion NVARCHAR(200) NOT NULL,
        CreadoPor NVARCHAR(100) NULL,
        FechaRegistro DATETIME NOT NULL DEFAULT GETDATE(),
        CONSTRAINT UQ_HUB_EstacionesTickets_Estacion UNIQUE (Estacion)
    );
END