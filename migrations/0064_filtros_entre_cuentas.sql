-- ============================================================================
-- Migración 0064: Reglas de filtro entre cuentas de Mailbox
-- ============================================================================
-- Permite que una regla de filtro mueva un mensaje de una cuenta a una carpeta
-- de OTRA cuenta asignada al mismo usuario.
-- Si IdCuentaDestino y CarpetaDestino están NULL, la regla se aplica dentro
-- de la misma cuenta (comportamiento actual).
-- ============================================================================

IF NOT EXISTS (SELECT * FROM sys.columns WHERE object_id = OBJECT_ID(N'[dbo].[HUB_MailboxReglas]') AND name = N'IdCuentaDestino')
BEGIN
    ALTER TABLE [dbo].[HUB_MailboxReglas] ADD [IdCuentaDestino] INT NULL;
    ALTER TABLE [dbo].[HUB_MailboxReglas] ADD CONSTRAINT [FK_HUB_MailboxReglas_IdCuentaDestino] FOREIGN KEY ([IdCuentaDestino]) REFERENCES [dbo].[HUB_MailboxCuentas]([Id]);
END

IF NOT EXISTS (SELECT * FROM sys.columns WHERE object_id = OBJECT_ID(N'[dbo].[HUB_MailboxReglas]') AND name = N'CarpetaDestino')
BEGIN
    ALTER TABLE [dbo].[HUB_MailboxReglas] ADD [CarpetaDestino] NVARCHAR(200) NULL;
END

CREATE INDEX IF NOT EXISTS [IX_HUB_MailboxReglas_IdCuentaDestino] ON [dbo].[HUB_MailboxReglas]([IdCuentaDestino]) WHERE [IdCuentaDestino] IS NOT NULL;
