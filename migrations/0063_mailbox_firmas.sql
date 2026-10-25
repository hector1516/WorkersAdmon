-- ============================================================================
-- Migración 0063: Firmas de correo de Mailbox
-- ============================================================================
-- Tabla de firmas por usuario. Cada usuario puede tener varias firmas y
-- marcar una como predeterminada. La firma se aplica a todas las cuentas
-- IMAP asignadas a ese usuario.
-- ============================================================================

IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[HUB_MailboxFirmas]') AND type in (N'U'))
BEGIN
    CREATE TABLE [dbo].[HUB_MailboxFirmas] (
        [Id] INT IDENTITY(1,1) PRIMARY KEY,
        [IdUsuario] INT NOT NULL,
        [Nombre] NVARCHAR(200) NOT NULL,
        [Html] NVARCHAR(MAX) NOT NULL,
        [Texto] NVARCHAR(MAX) NULL,
        [Activa] BIT NOT NULL DEFAULT 1,
        [Predeterminada] BIT NOT NULL DEFAULT 0,
        [Creado] DATETIME NOT NULL DEFAULT GETDATE(),
        [Modificado] DATETIME NOT NULL DEFAULT GETDATE(),
        CONSTRAINT [FK_HUB_MailboxFirmas_IdUsuario] FOREIGN KEY ([IdUsuario]) REFERENCES [dbo].[HUB_Users]([Id])
    );
    CREATE INDEX [IX_HUB_MailboxFirmas_IdUsuario] ON [dbo].[HUB_MailboxFirmas]([IdUsuario]);
    CREATE INDEX [IX_HUB_MailboxFirmas_Predeterminada] ON [dbo].[HUB_MailboxFirmas]([IdUsuario], [Predeterminada]);
END

-- ============================================================================
-- Plantillas predefinidas de firma (sistema, no por usuario)
-- Contienen placeholders: {{nombre}}, {{cargo}}, {{telefono}}, {{correo}},
-- {{direccion}}, {{web}}, {{logo}}, {{foto}}
-- ============================================================================

IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[HUB_MailboxFirmasPlantillas]') AND type in (N'U'))
BEGIN
    CREATE TABLE [dbo].[HUB_MailboxFirmasPlantillas] (
        [Id] INT IDENTITY(1,1) PRIMARY KEY,
        [Nombre] NVARCHAR(200) NOT NULL,
        [Descripcion] NVARCHAR(500) NULL,
        [Html] NVARCHAR(MAX) NOT NULL,
        [Categoria] NVARCHAR(100) NULL,
        [Activa] BIT NOT NULL DEFAULT 1,
        [Orden] INT NOT NULL DEFAULT 0,
        [Creado] DATETIME NOT NULL DEFAULT GETDATE()
    );
    CREATE INDEX [IX_HUB_MailboxFirmasPlantillas_Categoria] ON [dbo].[HUB_MailboxFirmasPlantillas]([Categoria], [Orden]);
END

-- ============================================================================
-- Sembrar plantillas predefinidas de ECCSA
-- ============================================================================

-- Plantilla: Corporativa ECCSA (tabla, logo izquierda, datos a la derecha)
IF NOT EXISTS (SELECT 1 FROM [dbo].[HUB_MailboxFirmasPlantillas] WHERE [Nombre] = 'Corporativa ECCSA')
BEGIN
    INSERT INTO [dbo].[HUB_MailboxFirmasPlantillas] ([Nombre], [Descripcion], [Html], [Categoria], [Orden])
    VALUES (
        'Corporativa ECCSA',
        'Firma corporativa con tabla, logo de ECCSA a la izquierda y datos del usuario a la derecha. Ideal para uso diario.',
        '<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN" "http://www.w3.org/TR/xhtml1/DTD/xhtml1-transitional.dtd"><html xmlns="http://www.w3.org/1999/xhtml"><head><meta http-equiv="Content-Type" content="text/html; charset=UTF-8" /></head><body style="margin:0;padding:0;font-family:Segoe UI,Tahoma,Geneva,sans-serif;font-size:13px;color:#333333;"><table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0"><tr><td align="left" valign="top" style="padding:0 0 10px 0;"><table role="presentation" cellspacing="0" cellpadding="0" border="0"><tr><td align="left" valign="middle" style="padding-right:12px;"><img src="cid:eccsa-logo" width="48" height="48" alt="ECCSA" style="display:block;border:0;" /></td><td align="left" valign="middle" style="font-family:Segoe UI,Tahoma,Geneva,sans-serif;font-size:14px;color:#1E293B;"><div style="font-weight:bold;color:#FF6B00;font-size:15px;">{{nombre}}</div><div style="font-size:12px;color:#64748B;">{{cargo}}</div></td></tr></table></td></tr><tr><td align="left" valign="top" style="padding:8px 0 0 0;border-top:1px solid #FF6B00;"><table role="presentation" cellspacing="0" cellpadding="0" border="0"><tr><td align="left" valign="top" style="padding-right:16px;"><div style="font-size:12px;color:#64748B;">📞 {{telefono}}</div></td><td align="left" valign="top" style="padding-right:16px;"><div style="font-size:12px;color:#64748B;">✉️ {{correo}}</div></td><td align="left" valign="top"><div style="font-size:12px;color:#64748B;">🌐 {{web}}</div></td></tr></table></td></tr><tr><td align="left" valign="top" style="padding:6px 0 0 0;"><div style="font-size:11px;color:#94A3B8;">{{direccion}}</div></td></tr></table></body></html>',
        'corporativa',
        1
    );
END

-- Plantilla: Moderna ECCSA (barra lateral naranja, datos limpios)
IF NOT EXISTS (SELECT 1 FROM [dbo].[HUB_MailboxFirmasPlantillas] WHERE [Nombre] = 'Moderna ECCSA')
BEGIN
    INSERT INTO [dbo].[HUB_MailboxFirmasPlantillas] ([Nombre], [Descripcion], [Html], [Categoria], [Orden])
    VALUES (
        'Moderna ECCSA',
        'Diseño moderno con barra lateral naranja de ECCSA, foto del usuario opcional y tipografía limpia.',
        '<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN" "http://www.w3.org/TR/xhtml1/DTD/xhtml1-transitional.dtd"><html xmlns="http://www.w3.org/1999/xhtml"><head><meta http-equiv="Content-Type" content="text/html; charset=UTF-8" /></head><body style="margin:0;padding:0;font-family:Segoe UI,Tahoma,Geneva,sans-serif;font-size:13px;color:#333333;"><table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0"><tr><td align="left" valign="top" style="padding:0;"><table role="presentation" cellspacing="0" cellpadding="0" border="0" style="background:#FFFFFF;border-left:4px solid #FF6B00;"><tr><td align="left" valign="top" style="padding:10px 14px 10px 14px;"><div style="font-weight:bold;color:#1E293B;font-size:14px;">{{nombre}}</div><div style="font-size:12px;color:#FF6B00;font-weight:600;">{{cargo}}</div><div style="padding-top:6px;font-size:12px;color:#64748B;"><div style="padding-bottom:2px;">📞 {{telefono}}</div><div style="padding-bottom:2px;">✉️ {{correo}}</div><div style="padding-bottom:2px;">🌐 {{web}}</div></div><div style="padding-top:8px;border-top:1px solid #E2E8F0;font-size:11px;color:#94A3B8;">{{direccion}}</div></td></tr></table></td></tr></table></body></html>',
        'moderna',
        2
    );
END

-- Plantilla: Simple ECCSA (texto limpio, ideal para respuestas)
IF NOT EXISTS (SELECT 1 FROM [dbo].[HUB_MailboxFirmasPlantillas] WHERE [Nombre] = 'Simple ECCSA')
BEGIN
    INSERT INTO [dbo].[HUB_MailboxFirmasPlantillas] ([Nombre], [Descripcion], [Html], [Categoria], [Orden])
    VALUES (
        'Simple ECCSA',
        'Firma mínima y elegante. Solo datos esenciales con el logo de ECCSA. Ideal para respuestas rápidas y cadenas de correo.',
        '<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN" "http://www.w3.org/TR/xhtml1/DTD/xhtml1-transitional.dtd"><html xmlns="http://www.w3.org/1999/xhtml"><head><meta http-equiv="Content-Type" content="text/html; charset=UTF-8" /></head><body style="margin:0;padding:0;font-family:Segoe UI,Tahoma,Geneva,sans-serif;font-size:13px;color:#333333;"><table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0"><tr><td align="left" valign="top" style="padding:0 0 10px 0;"><img src="cid:eccsa-logo" width="32" height="32" alt="ECCSA" style="display:block;border:0;" /></td></tr><tr><td align="left" valign="top" style="padding:4px 0 0 0;"><div style="font-weight:bold;color:#1E293B;font-size:13px;">{{nombre}}</div><div style="font-size:12px;color:#FF6B00;">{{cargo}}</div><div style="padding-top:4px;font-size:12px;color:#64748B;">{{telefono}} · {{correo}} · {{web}}</div><div style="padding-top:4px;font-size:11px;color:#94A3B8;">{{direccion}}</div></td></tr></table></body></html>',
        'simple',
        3
    );
END

-- ============================================================================
-- Logo de ECCSA como imagen de firma del sistema
-- (Se asume que el archivo eccsa_logo.png ya está en el volumen de firmas;
--  si no, se puede cargar desde la app. Esta fila es solo un marcador.)
-- ============================================================================

-- Nota: HUB_MailboxFirmasImagenes ya existe y se usa en smtp.py para
-- resolver las imágenes inline (cid:) de las firmas al enviar.
