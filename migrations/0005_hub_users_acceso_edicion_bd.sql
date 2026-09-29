-- 0005_hub_users_acceso_edicion_bd.sql
-- Migración: agrega columna AccesoEdicionBD a HUB_Users para el módulo Edición BD.

IF OBJECT_ID('dbo.HUB_Users', 'U') IS NOT NULL
   AND COL_LENGTH('dbo.HUB_Users', 'AccesoEdicionBD') IS NULL
BEGIN
    ALTER TABLE dbo.HUB_Users ADD AccesoEdicionBD BIT NOT NULL DEFAULT 0;
END
GO
