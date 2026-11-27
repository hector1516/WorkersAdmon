-- 0072 · ECCSita: roast por persona desde el grupo
--
-- `HUB_GrupoPerfiles` guarda UNA mencion util por renglon: la oracion del grupo
-- donde aparecio el alias de un usuario registrado. El bot la usa para el
-- roast (`wa_persona.sarcasmo`) y el panel la lista, la aprueba o la borra.
--
--   Texto   -> la oracion recortada (max. 500; la columna es NVARCHAR(500)).
--   Autor   -> quien lo dijo, SOLO para el panel: el bot nunca lo menciona.
--   Hash    -> sha256(chat + texto normalizado). Junto con IdUsuario forma el
--              indice unico, que impide guardar dos veces lo mismo aunque
--              OpenWA reentregue el mensaje.
--   Anio/Semana ISO de la Fecha (hora de Mexico), igual que HUB_GrupoMensajes.
--   Tipo    -> BROMA | APODO | INSISTENCIA.
--   Aprobado -> 1 = el bot puede usarlo; 0 = oculto (se conserva para el panel).
--
-- Idempotente: se puede ejecutar mas de una vez.
IF OBJECT_ID('dbo.HUB_GrupoPerfiles', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.HUB_GrupoPerfiles (
        Id          INT IDENTITY(1,1) NOT NULL
            CONSTRAINT PK_HUB_GrupoPerfiles PRIMARY KEY,
        IdUsuario   INT            NOT NULL,
        Texto       NVARCHAR(500)  NOT NULL,
        Autor       NVARCHAR(120)  NULL,
        ChatId      VARCHAR(80)    NOT NULL,
        Fecha       DATETIME       NOT NULL,
        Anio        INT            NOT NULL,
        Semana      INT            NOT NULL,
        Tipo        VARCHAR(20)    NOT NULL,
        Aprobado    BIT            NOT NULL
            CONSTRAINT DF_HUB_GrupoPerfiles_Aprobado DEFAULT 1,
        Hash        CHAR(64)       NOT NULL
    );
    PRINT 'HUB_GrupoPerfiles creada';
END
ELSE
    PRINT 'HUB_GrupoPerfiles ya existe';
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes
               WHERE name = 'UX_HUB_GrupoPerfiles_UsuarioHash'
                 AND object_id = OBJECT_ID('dbo.HUB_GrupoPerfiles'))
    CREATE UNIQUE NONCLUSTERED INDEX UX_HUB_GrupoPerfiles_UsuarioHash
        ON dbo.HUB_GrupoPerfiles (IdUsuario, Hash);
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes
               WHERE name = 'IX_HUB_GrupoPerfiles_UsuarioFecha'
                 AND object_id = OBJECT_ID('dbo.HUB_GrupoPerfiles'))
    CREATE NONCLUSTERED INDEX IX_HUB_GrupoPerfiles_UsuarioFecha
        ON dbo.HUB_GrupoPerfiles (IdUsuario, Fecha);
GO

-- Interruptor del roast del bot. El MERGE solo siembra: una clave existente no
-- se toca. `wa_grupo_alias` NO se siembra: lo pone el dueno si quiere alias
-- extra (JSON {"2":["hector","hech"], ...}).
MERGE dbo.HUB_Config AS t
USING (VALUES
    ('wa_roast_grupo', '1')
) AS s(Clave, Valor)
    ON t.Clave = s.Clave
WHEN NOT MATCHED THEN
    INSERT (Clave, Valor) VALUES (s.Clave, s.Valor);
GO

PRINT '0072_eccsita_roast.sql aplicada'
GO
