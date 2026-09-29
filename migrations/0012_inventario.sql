-- 0012_inventario.sql
-- Migración: módulo Inventario (ítems + categorías) y permiso AccesoInventario.
-- 1) Tabla HUB_InventarioCategorias (catálogo con CRUD propio).
-- 2) Tabla HUB_Inventario (ítems; Categoria se selecciona del catálogo).
-- 3) Columna AccesoInventario en HUB_Users (permiso del módulo).

IF OBJECT_ID('dbo.HUB_InventarioCategorias', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.HUB_InventarioCategorias (
        IdCategoria INT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        Categoria NVARCHAR(80) NOT NULL,
        FechaAlta DATETIME NOT NULL DEFAULT GETDATE()
    );
END
GO

IF OBJECT_ID('dbo.HUB_Inventario', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.HUB_Inventario (
        IdInventario INT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        IdCategoria INT NULL REFERENCES dbo.HUB_InventarioCategorias(IdCategoria),
        Marca NVARCHAR(120) NULL,
        Modelo NVARCHAR(120) NULL,
        Descripcion NVARCHAR(500) NULL,
        Cantidad INT NOT NULL DEFAULT 0,
        StockMinimo INT NOT NULL DEFAULT 0,
        Ubicacion NVARCHAR(120) NULL,
        Proveedor NVARCHAR(120) NULL,
        Precio DECIMAL(18,2) NULL,
        FechaActualizado DATETIME NOT NULL DEFAULT GETDATE()
    );
END
GO

IF COL_LENGTH('dbo.HUB_Users', 'AccesoInventario') IS NULL
    ALTER TABLE dbo.HUB_Users ADD AccesoInventario BIT NOT NULL DEFAULT 0;
GO
