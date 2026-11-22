-- 0063 · Estado y motivos de los vales (incidente 2026-11-22)
--
-- Qué pasó: el worker emitió 9 vales de $777 para una solicitud de $500. La
-- app no tenía dónde guardar POR QUÉ falló, ni el monto realmente emitido, ni
-- cuántos intentos lleva. Se agregan esas columnas y un candado de unicidad
-- por auto y día.
--
-- Sólo agrega columnas y un índice: no toca datos existentes.

IF COL_LENGTH('dbo.HUB_SolicitudVales', 'Motivo') IS NULL
BEGIN
    ALTER TABLE dbo.HUB_SolicitudVales ADD Motivo NVARCHAR(200) NULL;
    PRINT 'columna Motivo agregada';
END
GO

IF COL_LENGTH('dbo.HUB_SolicitudVales', 'MotivoCodigo') IS NULL
BEGIN
    ALTER TABLE dbo.HUB_SolicitudVales ADD MotivoCodigo NVARCHAR(40) NULL;
    PRINT 'columna MotivoCodigo agregada';
END
GO

IF COL_LENGTH('dbo.HUB_SolicitudVales', 'Intentos') IS NULL
BEGIN
    ALTER TABLE dbo.HUB_SolicitudVales
        ADD Intentos INT NOT NULL CONSTRAINT DF_AsisVales_Intentos DEFAULT (0);
    PRINT 'columna Intentos agregada';
END
GO

IF COL_LENGTH('dbo.HUB_SolicitudVales', 'LockHasta') IS NULL
BEGIN
    ALTER TABLE dbo.HUB_SolicitudVales ADD LockHasta DATETIME NULL;
    PRINT 'columna LockHasta agregada';
END
GO

IF COL_LENGTH('dbo.HUB_SolicitudVales', 'MontoEmitido') IS NULL
BEGIN
    ALTER TABLE dbo.HUB_SolicitudVales ADD MontoEmitido DECIMAL(10,2) NULL;
    PRINT 'columna MontoEmitido agregada';
END
GO

-- Día de la solicitud, para poder indexar «un vale por auto y día».
IF COL_LENGTH('dbo.HUB_SolicitudVales', 'FechaDia') IS NULL
BEGIN
    ALTER TABLE dbo.HUB_SolicitudVales
        ADD FechaDia AS CAST(FechaSolicitud AS DATE) PERSISTED;
    PRINT 'columna calculada FechaDia agregada';
END
GO

-- Índice único por auto y día, salvo cancelados. Si ya hubiera duplicados no
-- se crea (y avisa), porque el índice fallaría y tumbaría la migración.
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'UX_Vales_Auto_Dia')
BEGIN
    IF EXISTS (
        SELECT 1 FROM HUB_SolicitudVales
        WHERE Estatus <> 'CANCELADO'
        GROUP BY IdAutomovil, FechaDia
        HAVING COUNT(*) > 1
    )
    BEGIN
        PRINT 'AVISO: hay autos con mas de un vale el mismo dia; NO se crea el indice unico';
    END
    ELSE
    BEGIN
        CREATE UNIQUE INDEX UX_Vales_Auto_Dia
            ON dbo.HUB_SolicitudVales (IdAutomovil, FechaDia)
            WHERE Estatus <> 'CANCELADO';
        PRINT 'indice unico UX_Vales_Auto_Dia creado';
    END
END
ELSE
    PRINT 'UX_Vales_Auto_Dia ya existe';
GO
