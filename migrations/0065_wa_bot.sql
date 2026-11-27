-- 0065 - Receptor de mensajes entrantes de WhatsApp (OpenWA)
--
-- El panel ya manda avisos con OpenWA (0043). Esto es el otro sentido:
-- OpenWA reenvia por webhook cada mensaje que llega a la sesion y el panel
-- lo guarda aqui. En esta fase SOLO se guarda; el bot que contesta es la
-- siguiente.
--
--   HUB_WhatsappEntrante       -> un renglon por mensaje recibido. `Crudo` es
--                                 el JSON tal como llego, para poder reprocesar
--                                 cuando OpenWA cambie el sobre.
--   HUB_WhatsappConversaciones -> una fila por chat: a quien pertenece, en que
--                                 estado va (BOT hasta que alguien la toma) y
--                                 cuando fue el ultimo mensaje.
--
-- El token del webhook NO se siembra: se pega en HUB_Config con la clave
-- `wa_bot_token` y viaja en la ruta (/api/wa/entrante/<token>), porque OpenWA
-- no siempre conserva cabeceras propias.
--
-- Idempotente: se puede ejecutar mas de una vez.
IF OBJECT_ID('dbo.HUB_WhatsappEntrante', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.HUB_WhatsappEntrante (
        Id         INT IDENTITY(1,1) NOT NULL
            CONSTRAINT PK_HUB_WhatsappEntrante PRIMARY KEY,
        IdMensaje  VARCHAR(120)  NULL,
        ChatId     VARCHAR(80)   NOT NULL,
        Telefono   VARCHAR(20)   NULL,
        Nombre     NVARCHAR(120) NULL,
        Texto      NVARCHAR(2000) NULL,
        Fecha      DATETIME      NOT NULL
            CONSTRAINT DF_HUB_WhatsappEntrante_Fecha DEFAULT GETDATE(),
        Estado     VARCHAR(20)   NOT NULL
            CONSTRAINT DF_HUB_WhatsappEntrante_Estado DEFAULT 'NUEVO',
        IdUsuario  INT           NULL,
        IdCliente  VARCHAR(10)   NULL,
        Crudo      NVARCHAR(MAX) NULL
    );
    PRINT 'HUB_WhatsappEntrante creada';
END
ELSE
    PRINT 'HUB_WhatsappEntrante ya existe';
GO

-- OpenWA reintenta el webhook si no recibe 200: el mismo mensaje puede llegar
-- dos veces. El indice unico filtrado es la garantia final (la revision previa
-- en wa_webhook.py evita el error, esto lo respalda ante dos entregas a la vez).
IF NOT EXISTS (SELECT 1 FROM sys.indexes
               WHERE name = 'UX_HUB_WhatsappEntrante_IdMensaje'
                 AND object_id = OBJECT_ID('dbo.HUB_WhatsappEntrante'))
    CREATE UNIQUE NONCLUSTERED INDEX UX_HUB_WhatsappEntrante_IdMensaje
        ON dbo.HUB_WhatsappEntrante (IdMensaje)
        WHERE IdMensaje IS NOT NULL;
GO

IF OBJECT_ID('dbo.HUB_WhatsappConversaciones', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.HUB_WhatsappConversaciones (
        ChatId     VARCHAR(80)  NOT NULL
            CONSTRAINT PK_HUB_WhatsappConversaciones PRIMARY KEY,
        IdUsuario  INT          NULL,
        IdCliente  VARCHAR(10)  NULL,
        Estado     VARCHAR(20)  NOT NULL
            CONSTRAINT DF_HUB_WhatsappConversaciones_Estado DEFAULT 'BOT',
        AsignadoA  INT          NULL,
        Ultima     DATETIME     NOT NULL
            CONSTRAINT DF_HUB_WhatsappConversaciones_Ultima DEFAULT GETDATE()
    );
    PRINT 'HUB_WhatsappConversaciones creada';
END
ELSE
    PRINT 'HUB_WhatsappConversaciones ya existe';
GO
