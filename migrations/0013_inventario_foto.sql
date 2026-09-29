-- 0013_inventario_foto.sql
-- Migración: agrega la columna Foto a HUB_Inventario (foto del ítem, base64 data-URI).
-- Se almacena como NVARCHAR(MAX) siguiendo el patrón de FirmaConformidad (firmas en base64).

IF COL_LENGTH('dbo.HUB_Inventario', 'Foto') IS NULL
    ALTER TABLE dbo.HUB_Inventario ADD Foto NVARCHAR(MAX) NULL;
GO
