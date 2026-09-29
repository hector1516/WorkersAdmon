-- Migración 0037: Códigos SAT auto-alimentados (Plan: PlanesFuturos.md §1).
-- REGLA DEL DESARROLLO: solo se AGREGAN tablas nuevas; ninguna tabla existente
-- se altera ni se tocan sus datos (por eso el snapshot SAT va en una tabla
-- sidecar y NO como columnas sobre Partidas/RemisionPartidas).
--
-- 1) HUB_SatArticulos : índice maestro de artículos de la empresa con sus claves
--    SAT CFDI 4.0. Se llena solo: al guardar una partida → lookup local por
--    ClaveNormalizada → si no hay hit, reglas de keywords → si no, 1 llamada a
--    Gemini (mínimo tokens). UNIQUE en ClaveNormalizada = nunca re-preguntar.
-- 2) HUB_PartidasSat  : snapshot SAT por partida (Folio + Partida), para que
--    partidas viejas no cambien si luego se corrige el índice y para que el
--    PDF (cotización y remisión) pueda imprimirlos sin tocar la tabla Partidas.
--
-- NOTA: la remisión resuelve sus códigos SAT con un JOIN:
--    RemisionPartidas.Partida + IndiceRemisiones.FolioCotizacion → HUB_PartidasSat
-- (sin columnas nuevas en RemisionPartidas).

IF OBJECT_ID('dbo.HUB_SatArticulos', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.HUB_SatArticulos (
        IdArticulo         INT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        ClaveNormalizada   NVARCHAR(200) NOT NULL,   -- lower/trim de la descripción (llave de lookup)
        DescripcionEjemplo NVARCHAR(500) NULL,       -- 1ª descripción que creó el registro
        ClaveProdServ      CHAR(8) NOT NULL,         -- c_ProductoServicio SAT (8 dígitos)
        ClaveUnidad        CHAR(3) NOT NULL,         -- c_Unidad SAT (ej. H87, E48, KGM)
        DescripcionClave   NVARCHAR(200) NULL,       -- texto/razón sugerida (para UI)
        Fuente             VARCHAR(10) NOT NULL DEFAULT 'IA',  -- 'IA' | 'MANUAL' | 'SEED'
        Confianza          TINYINT NULL,             -- opcional 0-100
        Activo             BIT NOT NULL DEFAULT 1,
        FechaAlta          DATETIME NOT NULL DEFAULT GETDATE(),
        FechaUso           DATETIME NULL             -- última vez que se reusó (métrica)
    );
    CREATE UNIQUE INDEX IX_SatArticulos_Clave ON dbo.HUB_SatArticulos (ClaveNormalizada);
END
GO

IF OBJECT_ID('dbo.HUB_PartidasSat', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.HUB_PartidasSat (
        Id             INT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        Folio          NUMERIC(18,0) NOT NULL,       -- IndiceMateriales.Folio (CM)
        Partida        INT NOT NULL,                 -- Partidas.Partida
        ClaveNormalizada NVARCHAR(200) NOT NULL,     -- descripción con la que se resolvió (detecta cambios)
        ClaveProdServ  CHAR(8) NOT NULL,
        ClaveUnidad    CHAR(3) NOT NULL,
        Fuente         VARCHAR(10) NOT NULL DEFAULT 'IA',  -- 'IA' | 'MANUAL' | 'SEED'
        FechaAlta      DATETIME NOT NULL DEFAULT GETDATE(),
        FechaAct       DATETIME NOT NULL DEFAULT GETDATE()
    );
    CREATE UNIQUE INDEX IX_PartidasSat_Coordenada ON dbo.HUB_PartidasSat (Folio, Partida);
END
GO
