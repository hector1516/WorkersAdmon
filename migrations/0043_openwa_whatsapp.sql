-- ============================================================================
-- 0043_openwa_whatsapp.sql
-- Avisos por WhatsApp con OpenWA (gateway de whatsapp-web.js en el ServerVM).
--
--   HUB_WhatsappEventos -> catalogo de avisos: que se avisa, con que texto, a
--                          que numeros y si lleva archivo adjunto.
--   HUB_WhatsappQueue  -> bandeja de salida. El worker `openwa_worker` la
--                          consume y entrega por la API de OpenWA; si algo
--                          falla, se reintenta hasta 3 veces antes de marcarlo
--                          FALLADO (el error se guarda para verlo en el
--                          historial).
--
-- Es el espejo de las tablas de Telegram (HUB_TelegramEventos/Queue), pero NO
-- las reemplaza: los dos canales conviven. Lo que se dice lo arma
-- `notif_messages.py`, comun a los dos, para que el mismo dato no se vea
-- distinto segun por donde salga.
--
-- `Telefonos` es texto libre con los numeros separados por comas
-- ("5218123211516, 5512345678"), como lo pegaria quien administra. Se normalizan
-- al encolar (ver openwa_client.normalizar_chat_ids), no aqui: asi el mismo
-- numero escrito de varias formas no termina en dos chats distintos.
--
-- La API key NO se siembra a proposito: se pega desde la interfaz
-- (Notificaciones > Conexion > OpenWA) y vive en HUB_Config.
--
-- Idempotente: se puede ejecutar mas de una vez. Ejecutar primero en
-- ECCSA_Admon_Pruebas con apply_migrations.py.
-- ============================================================================

IF OBJECT_ID('dbo.HUB_WhatsappEventos', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.HUB_WhatsappEventos (
        IdEvento        NVARCHAR(50)  NOT NULL,   -- KILOMETROS, GOVALE_SALDO...
        Nombre          NVARCHAR(120) NOT NULL,   -- como se muestra en la pestana
        Descripcion     NVARCHAR(400) NULL,       -- para que se sepa que avisa
        PlantillaMensaje NVARCHAR(MAX) NOT NULL, -- texto con {placeholders}
        AdjuntarArchivo BIT           NOT NULL
            CONSTRAINT DF_HUB_WhatsappEventos_Adjuntar DEFAULT 0,
        Telefonos       NVARCHAR(600) NULL,       -- "5218123211516, 5512345678"
        Activo          BIT           NOT NULL
            CONSTRAINT DF_HUB_WhatsappEventos_Activo DEFAULT 0,
        Actualizado     DATETIME      NULL,
        CONSTRAINT PK_HUB_WhatsappEventos PRIMARY KEY (IdEvento)
    )
END
GO

IF OBJECT_ID('dbo.HUB_WhatsappQueue', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.HUB_WhatsappQueue (
        Id            BIGINT IDENTITY(1,1) NOT NULL,
        IdEvento      NVARCHAR(50)  NOT NULL,
        ChatId        NVARCHAR(50)  NOT NULL,      -- 5218123211516@c.us
        Texto         NVARCHAR(MAX) NULL,         -- ya renderizado
        Adjunto       VARBINARY(MAX) NULL,        -- PDF o foto
        AdjuntoNombre NVARCHAR(120) NULL,
        AdjuntoTipo   NVARCHAR(20)  NULL,         -- documento | imagen
        Estado        NVARCHAR(12)  NOT NULL
            CONSTRAINT DF_HUB_WhatsappQueue_Estado DEFAULT 'PENDIENTE',
        Intentos      TINYINT        NOT NULL
            CONSTRAINT DF_HUB_WhatsappQueue_Intentos DEFAULT 0,
        Error         NVARCHAR(500) NULL,
        Creado        DATETIME NOT NULL
            CONSTRAINT DF_HUB_WhatsappQueue_Creado DEFAULT GETDATE(),
        Enviado       DATETIME NULL,
        CONSTRAINT PK_HUB_WhatsappQueue PRIMARY KEY (Id)
    )

    -- El worker siempre pregunta lo mismo: lo pendiente, mas viejo primero.
    CREATE NONCLUSTERED INDEX IX_HUB_WhatsappQueue_Estado
        ON dbo.HUB_WhatsappQueue (Estado, Creado)
END
GO

-- GOVALE_SALDO: Saldo Go Vale diario
IF NOT EXISTS (SELECT 1 FROM dbo.HUB_WhatsappEventos WHERE IdEvento = N'GOVALE_SALDO')
    INSERT INTO dbo.HUB_WhatsappEventos
        (IdEvento, Nombre, Descripcion, PlantillaMensaje, AdjuntarArchivo, Telefonos, Activo)
    SELECT N'GOVALE_SALDO', N'Saldo Go Vale diario', N'Aviso con el saldo del monedero Go Vale, todos los dias.',
           REPLACE(N'💰 *Saldo Go Vale* — ${Saldo}~Estado: {Estado}~Umbral alerta: ${Umbral}~Última revisión: {UltimaRevision}', '~', CHAR(13) + CHAR(10)),
           0, NULL, 1
GO
-- GOVALE_SALDO_BAJO: Saldo Go Vale bajo
IF NOT EXISTS (SELECT 1 FROM dbo.HUB_WhatsappEventos WHERE IdEvento = N'GOVALE_SALDO_BAJO')
    INSERT INTO dbo.HUB_WhatsappEventos
        (IdEvento, Nombre, Descripcion, PlantillaMensaje, AdjuntarArchivo, Telefonos, Activo)
    SELECT N'GOVALE_SALDO_BAJO', N'Saldo Go Vale bajo', N'Se dispara cuando el saldo cae por debajo del umbral (2,000 por omision).',
           REPLACE(N'⚠️ *Saldo bajo en Go Vale* — ${Saldo}~Estado: {Estado}~Umbral de alerta: ${Umbral}~Última revisión: {UltimaRevision}~Conviene refactorizar antes de que se acaben los vales.', '~', CHAR(13) + CHAR(10)),
           0, NULL, 1
GO
-- KILOMETROS: Registro de kilometros
IF NOT EXISTS (SELECT 1 FROM dbo.HUB_WhatsappEventos WHERE IdEvento = N'KILOMETROS')
    INSERT INTO dbo.HUB_WhatsappEventos
        (IdEvento, Nombre, Descripcion, PlantillaMensaje, AdjuntarArchivo, Telefonos, Activo)
    SELECT N'KILOMETROS', N'Registro de kilometros', N'Aviso cada vez que se registra un odometro.',
           REPLACE(N'Registro de Kilomtros~🚗*{Automovil}*~📊 {Kilometros} km~👤 {Usuario}~📅 {Fecha} {Hora}', '~', CHAR(13) + CHAR(10)),
           0, NULL, 1
GO
-- REPORTE_SERVICIO: Reporte de servicio firmado
IF NOT EXISTS (SELECT 1 FROM dbo.HUB_WhatsappEventos WHERE IdEvento = N'REPORTE_SERVICIO')
    INSERT INTO dbo.HUB_WhatsappEventos
        (IdEvento, Nombre, Descripcion, PlantillaMensaje, AdjuntarArchivo, Telefonos, Activo)
    SELECT N'REPORTE_SERVICIO', N'Reporte de servicio firmado', N'Se adjunta el PDF del reporte que acaba de firmar el tecnico.',
           REPLACE(N'*{Folio}*~👥 {Cliente}~👤 Firmado: {UsuarioFirma}~📅 {FechaFirma} {HoraFirma}', '~', CHAR(13) + CHAR(10)),
           1, NULL, 1
GO
-- OXXOGAS_TICKET: Ticket OxxoGas registrado
IF NOT EXISTS (SELECT 1 FROM dbo.HUB_WhatsappEventos WHERE IdEvento = N'OXXOGAS_TICKET')
    INSERT INTO dbo.HUB_WhatsappEventos
        (IdEvento, Nombre, Descripcion, PlantillaMensaje, AdjuntarArchivo, Telefonos, Activo)
    SELECT N'OXXOGAS_TICKET', N'Ticket OxxoGas registrado', N'Se adjunta la foto del ticket.',
           REPLACE(N'Ticket de OXXO Gas~Fecha: {Fecha}~Nombre: {Nombre}~Cantidad: {Cantidad}~Folio ticket: {Folio}~Auto: {Auto}~Cliente: {Cliente}~Descripción: {Descripcion}', '~', CHAR(13) + CHAR(10)),
           1, NULL, 1
GO

-- Configuracion de la conexion. La API key se pega desde la interfaz; estas dos
-- no son secretas y se siembran para que la pestana abra con la direccion y la
-- sesion ya puestas (si OpenWA cambia, se corrigen ahi, no en una migracion).
IF NOT EXISTS (SELECT 1 FROM dbo.HUB_Config WHERE Clave = 'openwa_base_url')
    INSERT INTO dbo.HUB_Config (Clave, Valor) VALUES ('openwa_base_url', 'http://openwa:2785');
GO

IF NOT EXISTS (SELECT 1 FROM dbo.HUB_Config WHERE Clave = 'openwa_session_id')
    INSERT INTO dbo.HUB_Config (Clave, Valor) VALUES ('openwa_session_id', N'20a37407-8928-4d64-afea-15dd0b874207');
GO
