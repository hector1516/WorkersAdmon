-- 0020_usuario_foto.sql
-- Migración: agrega la columna Foto a HUB_Users (foto/imagen de perfil del usuario, base64 data-URI).
-- Se almacena como NVARCHAR(MAX) siguiendo el patrón de HUB_Inventario.Foto y FirmaConformidad (base64).
-- La imagen se carga desde el popup "🔑 Cambiar mi Contraseña" de app.py.

IF COL_LENGTH('dbo.HUB_Users', 'Foto') IS NULL
    ALTER TABLE dbo.HUB_Users ADD Foto NVARCHAR(MAX) NULL;
GO
