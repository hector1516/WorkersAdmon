-- 0010_webauthn_passkeys.sql
-- Migración: crear tabla de credenciales WebAuthn (passkeys/biometría) para login nativo.
-- Una fila por dispositivo: guarda la CLAVE PÚBLICA (COSE) del dispositivo, nunca la privada.

IF OBJECT_ID('dbo.HUB_PassKeys', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.HUB_PassKeys (
        Id INT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        IdUsuario INT NOT NULL,
        Credencial VARBINARY(1024) NOT NULL,      -- Credential ID (base64url → bytes)
        PublicKey VARBINARY(2048) NOT NULL,        -- Clave pública COSE (CBOR) codificada
        SignCount BIGINT NOT NULL DEFAULT 0,       -- Contador de uso (anti-replay, Clerk/plataforma)
        FechaRegistro DATETIME NOT NULL DEFAULT GETDATE(),
        FechaUltimoUso DATETIME NULL
    );
    CREATE UNIQUE INDEX IX_HUB_PassKeys_Credencial ON dbo.HUB_PassKeys (Credencial);
    CREATE INDEX IX_HUB_PassKeys_IdUsuario ON dbo.HUB_PassKeys (IdUsuario);
END
GO