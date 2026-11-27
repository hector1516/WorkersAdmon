-- 0071 · ECCSita: memoria semanal del grupo de WhatsApp
--
-- El webhook de OpenWA captura SOLO el grupo configurado en
-- `HUB_Config.wa_grupo_eccsita_id` (los demas grupos se siguen ignorando).
--
--   HUB_GrupoMensajes -> un renglon por mensaje capturado. La clave unica
--                        (ChatId, IdMensaje) impide guardar dos veces el mismo
--                        mensaje cuando OpenWA reintenta el webhook.
--
--   HUB_GrupoSemanas  -> un renglon por semana con su cronica. Estado:
--                        VACIA | CAPTURADA | RESUMIDA | REVISADA.
--                        GeneradoPor: BASICO (sin IA) | IA | persona.
--
-- Idempotente: se puede ejecutar mas de una vez.
IF OBJECT_ID('dbo.HUB_GrupoMensajes', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.HUB_GrupoMensajes (
        Id          INT IDENTITY(1,1) NOT NULL
            CONSTRAINT PK_HUB_GrupoMensajes PRIMARY KEY,
        ChatId      VARCHAR(80)   NOT NULL,
        IdMensaje   VARCHAR(120)  NOT NULL,
        AutorId     VARCHAR(80)   NULL,
        AutorNombre NVARCHAR(120) NULL,
        Tipo        VARCHAR(20)   NULL,
        Texto       NVARCHAR(2000) NULL,
        Fecha       DATETIME      NOT NULL,
        Semana      INT           NOT NULL,
        Anio        INT           NOT NULL,
        Crudo       NVARCHAR(MAX) NULL
    );
    PRINT 'HUB_GrupoMensajes creada';
END
ELSE
    PRINT 'HUB_GrupoMensajes ya existe';
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes
               WHERE name = 'UX_HUB_GrupoMensajes_Mensaje'
                 AND object_id = OBJECT_ID('dbo.HUB_GrupoMensajes'))
    CREATE UNIQUE NONCLUSTERED INDEX UX_HUB_GrupoMensajes_Mensaje
        ON dbo.HUB_GrupoMensajes (ChatId, IdMensaje);
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes
               WHERE name = 'IX_HUB_GrupoMensajes_Semana'
                 AND object_id = OBJECT_ID('dbo.HUB_GrupoMensajes'))
    CREATE NONCLUSTERED INDEX IX_HUB_GrupoMensajes_Semana
        ON dbo.HUB_GrupoMensajes (Anio, Semana);
GO

IF OBJECT_ID('dbo.HUB_GrupoSemanas', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.HUB_GrupoSemanas (
        Id              INT IDENTITY(1,1) NOT NULL
            CONSTRAINT PK_HUB_GrupoSemanas PRIMARY KEY,
        ChatId          VARCHAR(80)    NOT NULL,
        Anio            INT            NOT NULL,
        Semana          INT            NOT NULL,
        Inicio          DATE           NULL,
        Fin             DATE           NULL,
        Mensajes        INT            NOT NULL
            CONSTRAINT DF_HUB_GrupoSemanas_Mensajes DEFAULT 0,
        Participantes   INT            NOT NULL
            CONSTRAINT DF_HUB_GrupoSemanas_Participantes DEFAULT 0,
        Resumen         NVARCHAR(MAX)  NULL,
        Temas           NVARCHAR(MAX)  NULL,
        Personajes      NVARCHAR(MAX)  NULL,
        Momentos        NVARCHAR(MAX)  NULL,
        Estado          VARCHAR(20)    NOT NULL
            CONSTRAINT DF_HUB_GrupoSemanas_Estado DEFAULT 'VACIA',
        GeneradoPor     VARCHAR(20)    NULL,
        FechaGeneracion DATETIME       NULL
    );
    PRINT 'HUB_GrupoSemanas creada';
END
ELSE
    PRINT 'HUB_GrupoSemanas ya existe';
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes
               WHERE name = 'UX_HUB_GrupoSemanas_Semana'
                 AND object_id = OBJECT_ID('dbo.HUB_GrupoSemanas'))
    CREATE UNIQUE NONCLUSTERED INDEX UX_HUB_GrupoSemanas_Semana
        ON dbo.HUB_GrupoSemanas (ChatId, Anio, Semana);
GO

-- Valores por defecto. El MERGE solo siembra: una clave existente no se toca.
-- `wa_grupo_eccsita_id` NO se siembra: lo pone el dueno desde el panel.
MERGE dbo.HUB_Config AS t
USING (VALUES
    ('wa_grupo_captura',        '1'),
    ('wa_grupo_resumen',        '1'),
    ('wa_grupo_resumen_dia',    '1'),
    ('wa_grupo_resumen_hora',   '09:00'),
    ('wa_grupo_retencion_dias', '365')
) AS s(Clave, Valor)
    ON t.Clave = s.Clave
WHEN NOT MATCHED THEN
    INSERT (Clave, Valor) VALUES (s.Clave, s.Valor);
GO

PRINT '0071_eccsita.sql aplicada'
GO
