-- 0018_telegram_alertas.sql
-- Módulo Telegram: alertas automáticas del HUB vía Bot de Telegram.
-- Tablas: HUB_TelegramUsuarios, HUB_TelegramEventos, HUB_TelegramDestinatarios, HUB_TelegramQueue
-- Columna permiso: AccesoTelegram en HUB_Users
-- Config: token del bot en HUB_Config

-- 1) Tabla de vinculación usuarios del HUB ↔ chat_id de Telegram
IF OBJECT_ID('dbo.HUB_TelegramUsuarios', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.HUB_TelegramUsuarios (
        IdUsuario       INT NOT NULL PRIMARY KEY,
        ChatId          BIGINT NOT NULL,
        NombreTelegram  NVARCHAR(200) NULL,
        TelefonoMAC     VARCHAR(50) NULL,
        Activo          BIT NOT NULL DEFAULT 1,
        FechaVinculado  DATETIME NOT NULL DEFAULT GETDATE()
    );
END
GO

-- 2) Catálogo de eventos de alerta (plantillas configurables)
IF OBJECT_ID('dbo.HUB_TelegramEventos', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.HUB_TelegramEventos (
        IdEvento         VARCHAR(30) NOT NULL PRIMARY KEY,
        Nombre           NVARCHAR(100) NOT NULL,
        PlantillaMensaje NVARCHAR(1000) NOT NULL,
        AdjuntarArchivo  BIT NOT NULL DEFAULT 0,
        Activo           BIT NOT NULL DEFAULT 1
    );

    INSERT INTO dbo.HUB_TelegramEventos (IdEvento, Nombre, PlantillaMensaje, AdjuntarArchivo, Activo)
    VALUES
        ('KILOMETROS',       N'Registro de Kilometros',
                             N'*{Automovil}* — {Kilometros} km registrados por {Usuario}', 0, 1),
        ('OXXOGAS_TICKET',   N'Ticket OxxoGas',
                             N'*Ticket #{FolioTicket}* — {Automovil} — {Cliente}', 1, 1),
        ('REPORTE_SERVICIO', N'Reporte de Servicio (Firmado)',
                             N'*{Folio}* — {Cliente} — Firmado por {Tecnico}', 1, 1);
END
GO

-- 3) Destinatarios por evento (usuarios del HUB que reciben cada tipo de alerta)
IF OBJECT_ID('dbo.HUB_TelegramDestinatarios', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.HUB_TelegramDestinatarios (
        IdEvento   VARCHAR(30) NOT NULL,
        IdUsuario  INT NOT NULL,
        CONSTRAINT PK_TelegramDest PRIMARY KEY (IdEvento, IdUsuario),
        CONSTRAINT FK_TelegramDest_Evento FOREIGN KEY (IdEvento) REFERENCES dbo.HUB_TelegramEventos(IdEvento),
        CONSTRAINT FK_TelegramDest_User   FOREIGN KEY (IdUsuario) REFERENCES dbo.HUB_Users(Id)
    );
END
GO

-- 4) Cola de envíos (outbox) — el worker la consume
IF OBJECT_ID('dbo.HUB_TelegramQueue', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.HUB_TelegramQueue (
        Id              INT IDENTITY(1,1) PRIMARY KEY,
        IdEvento        VARCHAR(30) NOT NULL,
        ChatId          BIGINT NOT NULL,
        Texto           NVARCHAR(2000) NOT NULL,
        Adjunto         VARBINARY(MAX) NULL,
        AdjuntoNombre   NVARCHAR(200) NULL,
        Estado          VARCHAR(20) NOT NULL DEFAULT 'PENDIENTE',   -- PENDIENTE | ENVIADO | FALLADO
        Intentos        INT NOT NULL DEFAULT 0,
        UltimoIntento   DATETIME NULL,
        Respuesta       NVARCHAR(500) NULL,
        Creado          DATETIME NOT NULL DEFAULT GETDATE()
    );

    CREATE INDEX IX_TelegramQueue_Estado ON dbo.HUB_TelegramQueue(Estado, Creado);
END
GO

-- 5) Columna de permiso en HUB_Users
IF COL_LENGTH('dbo.HUB_Users', 'AccesoTelegram') IS NULL
    ALTER TABLE dbo.HUB_Users ADD AccesoTelegram BIT NOT NULL DEFAULT 0;
GO

-- 6) Token del bot en HUB_Config (valor inicial vacío, se configura desde la vista)
IF NOT EXISTS (SELECT 1 FROM dbo.HUB_Config WHERE Clave = 'telegram_bot_token')
    INSERT INTO dbo.HUB_Config (Clave, Valor) VALUES ('telegram_bot_token', '');
GO
