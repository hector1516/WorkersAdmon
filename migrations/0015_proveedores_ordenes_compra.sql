-- 0015_proveedores_ordenes_compra.sql
-- Migración: MÓDULOS NUEVOS "Proveedores" y "Órdenes de Compra (OC)".
-- 1) HUB_Proveedores : catálogo de proveedores (razón social), ID automático PROV###
--                     (se calcula en la capa Python, aquí solo la tabla).
-- 2) HUB_IndiceOC     : encabezado/índice de cada Orden de Compra (folio OC-AAAA-NNNN,
--                      proveedor, moneda, tipo de cambio, condiciones, estatus, PDF origen).
-- 3) HUB_OCPartidas   : detalle/líneas de la OC. Por partida lleva TiempoEntregaDias,
--                      FechaCompra y FechaRecibido para el semáforo/contador de llegada.
-- 4) Columna AccesoProveedores y AccesoOC en HUB_Users (permisos de los módulos).

IF OBJECT_ID('dbo.HUB_Proveedores', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.HUB_Proveedores (
        IdProveedor NVARCHAR(50) NOT NULL PRIMARY KEY,   -- 'PROV001', ...
        Nombre NVARCHAR(MAX) NOT NULL,                    -- Razón social / nombre del proveedor
        Contacto NVARCHAR(120) NULL,
        Telefono NVARCHAR(40) NULL,
        Email NVARCHAR(150) NULL,
        Activo BIT NOT NULL DEFAULT 1
    );
END
GO

IF OBJECT_ID('dbo.HUB_IndiceOC', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.HUB_IndiceOC (
        Id INT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        FolioOC NVARCHAR(40) NOT NULL,                 -- 'OC-AAAA-NNNN'
        IdProveedor NVARCHAR(50) NULL,
        Fecha DATE NOT NULL DEFAULT CAST(GETDATE() AS DATE),
        Condicion NVARCHAR(300) NULL,                  -- Condiciones de pago
        Moneda NVARCHAR(3) NOT NULL DEFAULT 'MXN',     -- 'USD' | 'MXN'
        TasaDolar DECIMAL(12,4) NOT NULL DEFAULT 0,
        Subtotal DECIMAL(18,2) NOT NULL DEFAULT 0,
        IVA DECIMAL(18,2) NOT NULL DEFAULT 0,
        TotalMXN DECIMAL(18,2) NOT NULL DEFAULT 0,     -- Total en moneda local (MXN) mostrado
        Notas NVARCHAR(MAX) NULL,
        Autor NVARCHAR(120) NULL,
        Estatus NVARCHAR(20) NOT NULL DEFAULT 'PENDIENTE', -- PENDIENTE/PARCIAL/COMPLETADA/CANCELADA
        FechaCreacion DATETIME NOT NULL DEFAULT GETDATE(),
        PdfNombre NVARCHAR(255) NULL,
        PdfAdjunto VARBINARY(MAX) NULL
    );
    CREATE UNIQUE INDEX IX_hub_indice_oc_folio ON dbo.HUB_IndiceOC (FolioOC);
END
GO

IF OBJECT_ID('dbo.HUB_OCPartidas', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.HUB_OCPartidas (
        FolioOC NVARCHAR(40) NOT NULL,
        Partida INT NOT NULL,
        Cantidad DECIMAL(12,2) NOT NULL DEFAULT 0,
        Descripcion NVARCHAR(MAX) NULL,
        Modelo NVARCHAR(120) NULL,
        PrecioUnitario DECIMAL(18,4) NOT NULL DEFAULT 0,
        PrecioTotal DECIMAL(18,2) NOT NULL DEFAULT 0,
        TiempoEntregaDias INT NOT NULL DEFAULT 0,
        FechaCompra DATE NULL,
        FechaRecibido DATE NULL                      -- NULL = aún no llega
    );
    ALTER TABLE dbo.HUB_OCPartidas ADD CONSTRAINT PK_HUB_OCPartidas
        PRIMARY KEY (FolioOC, Partida);
END
GO

IF COL_LENGTH('dbo.HUB_Users', 'AccesoProveedores') IS NULL
    ALTER TABLE dbo.HUB_Users ADD AccesoProveedores BIT NOT NULL DEFAULT 0;
GO

IF COL_LENGTH('dbo.HUB_Users', 'AccesoOC') IS NULL
    ALTER TABLE dbo.HUB_Users ADD AccesoOC BIT NOT NULL DEFAULT 0;
GO