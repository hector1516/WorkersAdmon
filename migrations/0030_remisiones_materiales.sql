-- Migracion 0030: Remisiones desde Cotizaciones de Materiales.
-- 1) IndiceRemisiones : encabezado inmutable de la remisión (folio RM-CM#####-NN,
--    snapshot de los datos de la cotización + quién la creó y cuándo).
-- 2) RemisionPartidas : partidas de la remisión (solo cantidad y descripción,
--    SIN precios). Se copian de Partidas al momento de crear y no se editan.
-- La remisión no se modifica tras creada; solo se puede eliminar.

IF OBJECT_ID('dbo.IndiceRemisiones', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.IndiceRemisiones (
        IdRemision INT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        FolioRemision VARCHAR(20) NOT NULL,           -- RM-CM#####-NN (único)
        FolioCotizacion NUMERIC(18,0) NOT NULL,       -- IndiceMateriales.Folio (CM)
        IdCliente VARCHAR(4) NOT NULL,                -- copia del encabezado CM
        Contacto VARCHAR(MAX) NOT NULL,
        Descripcion VARCHAR(MAX) NOT NULL,
        Autor VARCHAR(50) NOT NULL,                   -- autor de la cotización origen
        CreadoPor VARCHAR(50) NOT NULL,               -- usuario que creó la remisión
        IdUsuarioAsignado INT NULL,                   -- HUB user que debe firmar en Field (NULL = sin asignar)
        FirmaConformidad VARCHAR(MAX) NULL,           -- firma capturada en Field (base64 data URL, patrón ReportesServicio)
        FechaFirma DATETIME NULL,                     -- cuándo se firmó (NULL = sin firmar)
        FechaCreacion DATETIME NOT NULL DEFAULT GETDATE()
    );
    CREATE UNIQUE INDEX IX_remision_folio ON dbo.IndiceRemisiones (FolioRemision);
    CREATE INDEX IX_remision_cot ON dbo.IndiceRemisiones (FolioCotizacion);
    CREATE INDEX IX_remision_asignado ON dbo.IndiceRemisiones (IdUsuarioAsignado);
END
GO

-- Compatibilidad si 0030 ya se aplicó sin los campos de asignación/firma
IF OBJECT_ID('dbo.IndiceRemisiones', 'U') IS NOT NULL
AND COL_LENGTH('dbo.IndiceRemisiones', 'IdUsuarioAsignado') IS NULL
BEGIN
    ALTER TABLE dbo.IndiceRemisiones ADD IdUsuarioAsignado INT NULL;
    CREATE INDEX IX_remision_asignado ON dbo.IndiceRemisiones (IdUsuarioAsignado);
END
GO

IF OBJECT_ID('dbo.IndiceRemisiones', 'U') IS NOT NULL
AND COL_LENGTH('dbo.IndiceRemisiones', 'FirmaConformidad') IS NULL
BEGIN
    ALTER TABLE dbo.IndiceRemisiones ADD FirmaConformidad VARCHAR(MAX) NULL;
END
GO

IF OBJECT_ID('dbo.IndiceRemisiones', 'U') IS NOT NULL
AND COL_LENGTH('dbo.IndiceRemisiones', 'FechaFirma') IS NULL
BEGIN
    ALTER TABLE dbo.IndiceRemisiones ADD FechaFirma DATETIME NULL;
END
GO

IF OBJECT_ID('dbo.RemisionPartidas', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.RemisionPartidas (
        Id INT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        IdRemision INT NOT NULL REFERENCES dbo.IndiceRemisiones(IdRemision) ON DELETE CASCADE,
        Partida INT NOT NULL,                         -- número de partida en la CM origen
        Cantidad INT NOT NULL,                        -- cantidad editable al crear
        Descripcion VARCHAR(MAX) NOT NULL             -- copia (sin precios)
    );
    CREATE INDEX IX_rempart_rem ON dbo.RemisionPartidas (IdRemision);
END
GO
