-- Migración 0027: Configuración de guardado automático de PDFs en share SMB
-- Agrega claves en HUB_Config para la conexión SMB y ruta de montaje.

-- Configuración SMB para guardado automático de PDFs
IF NOT EXISTS (SELECT 1 FROM HUB_Config WHERE Clave = 'smb_share_path')
    INSERT INTO HUB_Config (Clave, Valor) VALUES ('smb_share_path', '');
GO
IF NOT EXISTS (SELECT 1 FROM HUB_Config WHERE Clave = 'smb_user')
    INSERT INTO HUB_Config (Clave, Valor) VALUES ('smb_user', '');
GO
IF NOT EXISTS (SELECT 1 FROM HUB_Config WHERE Clave = 'smb_password')
    INSERT INTO HUB_Config (Clave, Valor) VALUES ('smb_password', '');
GO
IF NOT EXISTS (SELECT 1 FROM HUB_Config WHERE Clave = 'smb_domain')
    INSERT INTO HUB_Config (Clave, Valor) VALUES ('smb_domain', '');
GO
IF NOT EXISTS (SELECT 1 FROM HUB_Config WHERE Clave = 'pdf_output_dir')
    INSERT INTO HUB_Config (Clave, Valor) VALUES ('pdf_output_dir', '');
GO

-- Permiso de acceso al módulo de configuración de PDFs
IF COL_LENGTH('dbo.HUB_Users', 'AccesoPdfConfig') IS NULL
    ALTER TABLE dbo.HUB_Users ADD AccesoPdfConfig BIT NOT NULL DEFAULT 0;
GO
