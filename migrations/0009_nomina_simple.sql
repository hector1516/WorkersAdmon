-- 0009_nomina_simple.sql
-- Migración: simplificar módulo de Nóminas.
-- 1) Tabla única HUB_Nomina (sueldo por usuario).
-- 2) DROP de las tablas de 0008 (semana, registros, préstamos, utilidades, aguinaldo, bitácora).

IF OBJECT_ID('dbo.HUB_Nomina', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.HUB_Nomina (
        Id INT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        IdUsuario INT NOT NULL,
        Sueldo DECIMAL(18,2) NOT NULL DEFAULT 0,
        FechaActualizado DATETIME NOT NULL DEFAULT GETDATE()
    );
END
GO

IF OBJECT_ID('dbo.HUB_NominaRegistro', 'U') IS NOT NULL
    DROP TABLE dbo.HUB_NominaRegistro;
GO

IF OBJECT_ID('dbo.HUB_NominaSemana', 'U') IS NOT NULL
    DROP TABLE dbo.HUB_NominaSemana;
GO

IF OBJECT_ID('dbo.HUB_NominaPrestamo', 'U') IS NOT NULL
    DROP TABLE dbo.HUB_NominaPrestamo;
GO

IF OBJECT_ID('dbo.HUB_NominaUtilidad', 'U') IS NOT NULL
    DROP TABLE dbo.HUB_NominaUtilidad;
GO

IF OBJECT_ID('dbo.HUB_NominaAguinaldo', 'U') IS NOT NULL
    DROP TABLE dbo.HUB_NominaAguinaldo;
GO

IF OBJECT_ID('dbo.HUB_NominaBitacora', 'U') IS NOT NULL
    DROP TABLE dbo.HUB_NominaBitacora;
GO
