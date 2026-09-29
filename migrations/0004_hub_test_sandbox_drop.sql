-- 0004_hub_test_sandbox_drop.sql
-- Elimina la tabla de PRUEBA HUB_TestSandbox creada por 0003.
-- Confirmación de limpieza tras validar el sistema de migraciones.

IF OBJECT_ID('dbo.HUB_TestSandbox', 'U') IS NOT NULL
BEGIN
    DROP TABLE dbo.HUB_TestSandbox;
END
GO