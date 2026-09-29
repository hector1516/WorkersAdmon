-- Migración: catálogo dedicado de contactos por cliente
-- Motivo: Field guardaba contactos nuevos INSERTando en IndiceMateriales
-- (con Descripcion=''), lo que creaba "cotizaciones de materiales" fantasma
-- en el HUB al crear un reporte. Ahora los contactos viven aquí.
-- Lectura en Field y HUB: UNION de esta tabla + contactos reales de IndiceMateriales.

IF OBJECT_ID('dbo.HUB_ContactosClientes', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.HUB_ContactosClientes (
        IdContacto  INT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        IdCliente   VARCHAR(4)  NOT NULL,
        Contacto    NVARCHAR(200) NOT NULL,
        CreadoPor   NVARCHAR(100) NULL,
        FechaRegistro DATETIME NOT NULL DEFAULT GETDATE(),
        CONSTRAINT UQ_HUB_ContactosClientes UNIQUE (IdCliente, Contacto)
    );

    -- Sembrar desde contactos ya existentes en cotizaciones (reales)
    INSERT INTO dbo.HUB_ContactosClientes (IdCliente, Contacto, CreadoPor, FechaRegistro)
    SELECT DISTINCT
        IM.IdCliente,
        LTRIM(RTRIM(IM.Contacto)),
        'seed',
        GETDATE()
    FROM dbo.IndiceMateriales IM
    WHERE IM.Contacto IS NOT NULL
      AND LTRIM(RTRIM(IM.Contacto)) <> ''
      AND IM.IdCliente IS NOT NULL
      AND LTRIM(RTRIM(IM.IdCliente)) <> '';
END
GO

-- Limpiar cotizaciones fantasma creadas por Field (Descripcion vacía y SIN partidas)
-- Solo borra encabezados vacíos sin partidas; las cotizaciones reales se conservan.
DELETE IM
FROM dbo.IndiceMateriales IM
WHERE (IM.Descripcion IS NULL OR LTRIM(RTRIM(IM.Descripcion)) = '')
  AND NOT EXISTS (
      SELECT 1 FROM dbo.Partidas P WHERE P.Folio = IM.Folio
  );
GO
