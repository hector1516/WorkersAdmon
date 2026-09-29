-- 0014_calculos_cotizaciones.sql
-- Migración: MÓDULO NUEVO "Cálculos de Cotizaciones" (Servicios y Materiales).
-- 1) Indice_calculocotizacion : índice/encabezado de cada cálculo (con campo para el
--    futuro folio de la cotización que lo consuma).
-- 2) CalculoCotizacionItem    : detalle/motor (Servicios desde reportes+manual, Materiales
--    desde inventario o manual; Materiales llevan Proveedor y TiempoEntrega por ítem).
-- 3) HUB_Config               : configuración global (tipo de cambio USD default editable).
-- 4) Columna AccesoCalculos en HUB_Users (permiso del módulo).

IF OBJECT_ID('dbo.Indice_calculocotizacion', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.Indice_calculocotizacion (
        Id BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        FolioCalculo NVARCHAR(40) NOT NULL,
        IdCliente NVARCHAR(8) NULL,
        Contacto NVARCHAR(120) NULL,
        Descripcion NVARCHAR(500) NULL,
        Departamento NVARCHAR(80) NULL,
        Elaboro NVARCHAR(120) NULL,
        FechaCreacion DATETIME NOT NULL DEFAULT GETDATE(),
        FechaActualizado DATETIME NOT NULL DEFAULT GETDATE(),
        IdCotizacionFuente NVARCHAR(128) NULL,
        Estatus NVARCHAR(20) NOT NULL DEFAULT 'BORRADOR',
        TasaDolar DECIMAL(12,4) NOT NULL DEFAULT 0,
        SubtotalServicios DECIMAL(18,2) NOT NULL DEFAULT 0,
        SubtotalMateriales DECIMAL(18,2) NOT NULL DEFAULT 0,
        Subtotal DECIMAL(18,2) NOT NULL DEFAULT 0,
        IVA DECIMAL(18,2) NOT NULL DEFAULT 0,
        Total DECIMAL(18,2) NOT NULL DEFAULT 0
    );
    CREATE UNIQUE INDEX IX_indice_calculo_folio ON dbo.Indice_calculocotizacion (FolioCalculo);
END
GO

IF OBJECT_ID('dbo.CalculoCotizacionItem', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.CalculoCotizacionItem (
        Id BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        IdCalculo BIGINT NOT NULL REFERENCES dbo.Indice_calculocotizacion(Id) ON DELETE CASCADE,
        Numero INT NOT NULL DEFAULT 1,
        Tipo VARCHAR(12) NOT NULL,            -- 'SERVICIO' | 'MATERIAL'
        Fuente VARCHAR(20) NULL,              -- 'reporte' | 'manual' | 'inventario'
        IdReporteOrigen INT NULL,             -- trazabilidad (ReportesServicio.IdReporte)
        Cantidad DECIMAL(12,2) NOT NULL DEFAULT 0,
        Descripcion NVARCHAR(500) NULL,
        Modelo NVARCHAR(120) NULL,
        Proveedor NVARCHAR(120) NULL,         -- SOLO Materiales (por ítem)
        TiempoEntrega NVARCHAR(120) NULL,     -- SOLO Materiales (por ítem)
        Tarifa DECIMAL(18,2) NOT NULL DEFAULT 0,
        Factor DECIMAL(6,4) NOT NULL DEFAULT 0,
        PrecioVentaUnit DECIMAL(18,2) NOT NULL DEFAULT 0,
        PrecioVentaTotal DECIMAL(18,2) NOT NULL DEFAULT 0
    );
    CREATE INDEX idx_calc_item_calculo ON dbo.CalculoCotizacionItem (IdCalculo);
END
GO

IF OBJECT_ID('dbo.HUB_Config', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.HUB_Config (
        Clave NVARCHAR(50) NOT NULL PRIMARY KEY,
        Valor NVARCHAR(500) NULL,
        Actualizado DATETIME NOT NULL DEFAULT GETDATE()
    );
    INSERT INTO dbo.HUB_Config (Clave, Valor) VALUES ('tipo_cambio_usd', '');
END
GO

IF COL_LENGTH('dbo.HUB_Users', 'AccesoCalculo') IS NULL
    ALTER TABLE dbo.HUB_Users ADD AccesoCalculo BIT NOT NULL DEFAULT 0;
GO