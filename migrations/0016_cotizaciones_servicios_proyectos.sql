-- 0016_cotizaciones_servicios_proyectos.sql
-- Migración: MÓDULO NUEVO "Cotizaciones Servicios y Proyectos".
-- 1) Indice_CotizacionesServProy : índice/encabezado (folio CSP-AAAA-NNNN, quién la hizo,
--    quién(es) realizaron el servicio, OC del cliente, factura, cálculo fuente, montos, Color).
-- 2) CotizacionServProyPartidas  : partidas tomadas de un cálculo (PrecioVentaUnit/Total) o
--    una sola partida resumen; editables.
-- 3) Historial_CotizacionesServProy : historial breve de modificaciones por folio.
-- 4) Se ELIMINA el módulo antiguo "Cotizaciones Reportes" (tabla CotizacionesReportes).
--    El permiso AccesoCotizacionesReportes de HUB_Users se reutiliza para este módulo nuevo.

IF OBJECT_ID('dbo.Indice_CotizacionesServProy', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.Indice_CotizacionesServProy (
        Id BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        Folio NVARCHAR(20) NOT NULL,
        IdCliente NVARCHAR(8) NULL,
        Contacto NVARCHAR(120) NULL,
        FechaCreacion DATETIME NOT NULL DEFAULT GETDATE(),
        FechaActualizado DATETIME NOT NULL DEFAULT GETDATE(),
        Descripcion NVARCHAR(MAX) NULL,
        Elaboro NVARCHAR(120) NULL,              -- usuario que elaboró la cotización
        Realizado NVARCHAR(400) NULL,            -- usuario(s) que realizaron el servicio
        OrdenCompraCliente NVARCHAR(120) NULL,   -- OC del cliente
        Factura NVARCHAR(120) NULL,              -- factura de ECCSA
        IdCalculoFuente BIGINT NULL,             -- FK a Indice_calculocotizacion.Id
        Color INT NULL,                          -- 0 pendiente, 1 entregada, 2 pagada
        Subtotal DECIMAL(18,2) NOT NULL DEFAULT 0,
        IVA DECIMAL(18,2) NOT NULL DEFAULT 0,
        Total DECIMAL(18,2) NOT NULL DEFAULT 0,
        ActualizadoPor NVARCHAR(120) NULL        -- quién hizo la última modificación
    );
    CREATE UNIQUE INDEX IX_cotservproy_folio ON dbo.Indice_CotizacionesServProy (Folio);
END
GO

IF OBJECT_ID('dbo.CotizacionServProyPartidas', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.CotizacionServProyPartidas (
        Id BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        IdCotizacion BIGINT NOT NULL REFERENCES dbo.Indice_CotizacionesServProy(Id) ON DELETE CASCADE,
        Partida INT NOT NULL DEFAULT 1,
        Tipo VARCHAR(12) NOT NULL,               -- 'SERVICIO' | 'MATERIAL'
        Cantidad DECIMAL(12,2) NOT NULL DEFAULT 0,
        Descripcion NVARCHAR(MAX) NULL,
        Modelo NVARCHAR(120) NULL,
        Proveedor NVARCHAR(120) NULL,
        TiempoEntrega NVARCHAR(120) NULL,
        PrecioVentaUnit DECIMAL(18,2) NOT NULL DEFAULT 0,
        PrecioVentaTotal DECIMAL(18,2) NOT NULL DEFAULT 0
    );
    CREATE INDEX idx_cotservproy_part_cot ON dbo.CotizacionServProyPartidas (IdCotizacion);
END
GO

IF OBJECT_ID('dbo.Historial_CotizacionesServProy', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.Historial_CotizacionesServProy (
        Id BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        Folio NVARCHAR(20) NOT NULL,
        Usuario NVARCHAR(120) NULL,
        Accion NVARCHAR(500) NULL,
        Fecha DATETIME NOT NULL DEFAULT GETDATE()
    );
    CREATE INDEX idx_hist_cotservproy_folio ON dbo.Historial_CotizacionesServProy (Folio);
END
GO

-- Respaldo del módulo antiguo "Cotizaciones Reportes":
-- 1) Copia íntegra de CotizacionesReportes a CotizacionesReportes_Backup.
-- 2) Copia los reportes de servicio que aún apuntan a un folio CRS-* (referencia
--    al módulo antiguo) a ReportesServicio_Backup_CRS para mantener trazabilidad.
-- 3) Limpia el folio CRS-* en ReportesServicio.Cotizacion (ya no aplica).
IF OBJECT_ID('dbo.CotizacionesReportes', 'U') IS NOT NULL
AND OBJECT_ID('dbo.CotizacionesReportes_Backup', 'U') IS NULL
BEGIN
    SELECT * INTO dbo.CotizacionesReportes_Backup FROM dbo.CotizacionesReportes;
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.tables WHERE name = 'ReportesServicio_Backup_CRS')
BEGIN
    SELECT * INTO dbo.ReportesServicio_Backup_CRS
    FROM dbo.ReportesServicio
    WHERE LTRIM(RTRIM(ISNULL(Cotizacion,''))) LIKE 'CRS-%';
END
GO

-- Limpia la referencia a folios del módulo antiguo (evita vínculos huérfanos en el nuevo flujo)
UPDATE dbo.ReportesServicio SET Cotizacion = NULL
WHERE LTRIM(RTRIM(ISNULL(Cotizacion,''))) LIKE 'CRS-%';
GO

-- Elimina el módulo antiguo "Cotizaciones Reportes"
IF OBJECT_ID('dbo.CotizacionesReportes', 'U') IS NOT NULL
    DROP TABLE dbo.CotizacionesReportes;
GO
