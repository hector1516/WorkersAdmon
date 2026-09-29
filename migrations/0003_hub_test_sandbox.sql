-- 0003_hub_test_sandbox.sql
-- Migración de PRUEBA: crea una tabla sandbox para validar el sistema de migraciones.
-- Se aplica SOLO en ECCSA_Admon_Pruebas. Se elimina con 0004_hub_test_sandbox_drop.sql

IF OBJECT_ID('dbo.HUB_TestSandbox', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.HUB_TestSandbox (
        Id            INT IDENTITY(1,1) PRIMARY KEY,
        Nombre        NVARCHAR(200) NOT NULL,
        CreadoEn      DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
        Activo        BIT NOT NULL DEFAULT 1
    );
END
GO