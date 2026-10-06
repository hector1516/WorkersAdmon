-- ============================================================================
-- 0056_avisos_push.sql
-- Avisos push por aplicación: suscripciones con columna `App` + cola interna.
--
-- PARA QUÉ
-- --------
-- El dispatcher de avisos vive en WorkersAdmon y manda a los usuarios de varias
-- apps (hoy Admon; después Field y Mailbox). La tabla que había
-- (HUB_PushSubscriptions) tiene DOS problemas que impiden que eso funcione:
--
--   1. **No tiene columna de app.** Una suscripción creada desde
--      admon.ecc-sa.com.mx queda indistinguible de una creada desde
--      field.ecc-sa.com.mx para el MISMO usuario. Al mandar un aviso, la
--      librería elegiría todas las suscripciones de ese usuario y llegaría
--      también la alerta de kilómetros al móvil. Notificaciones cruzadas entre
--      apps: exactamente el tipo de cosa que hace que la gente desactive los
--      permisos. Mailbox ya resolvió esto con una tabla propia (0050) porque en
--      aquel momento no había opción menos invasiva; aquí ya se va a tocar Field
--      de todos modos, así que se hace bien y una sola vez.
--
--   2. **No se puede indexar su Endpoint.** Es NVARCHAR y los endpoints de
--      Apple miden ~180 caracteres; el índice se pasa del tope de 900 bytes muy
--      rápido. Sin índice único, el mismo dispositivo suscrito dos veces recibe
--      cada aviso dos veces. Se resuelve con una columna calculada PERSISTED
--      con el hash (el patrón estándar cuando la columna excede el tope).
--
-- LA TABLA NUEVA, NO UN ALTER
-- ---------------------------
-- Se crea `HUB_PushSuscripciones` en vez de alterar `HUB_PushSubscriptions`:
-- la vieja la escribe `eccsa_db.save_push_subscription()` (suscrita por
-- `mcp_server` -> `/__push_subscribe__`) y la lee el bloque Push del panel. Se
-- deja como está, sin tocar, y el dispatcher usa la nueva. Cuando se quiera
-- apagar la vieja es un TRUNCATE, no una migración riesgosa sobre una tabla viva.
--
-- Idempotente: se puede ejecutar más de una vez.
-- Ejecutar primero en ECCSA_Admon_Pruebas con apply_migrations.py.
-- ============================================================================

-- ─── 1) Suscripciones push, particionadas por aplicación ─────────────────────
IF OBJECT_ID('dbo.HUB_PushSuscripciones', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.HUB_PushSuscripciones (
        Id              INT IDENTITY(1,1) NOT NULL,

        -- 'admon' | 'field' | 'mailbox' | <futura>. Es lo que evita que un
        -- aviso de una app llegue en el service worker de otra.
        App             NVARCHAR(20)   NOT NULL,

        IdUsuario       INT            NOT NULL,

        -- La URL del push service (Apple, Google, Mozilla). Los de Apple miden
        -- ~180 caracteres; de ahí el EndpointHash de abajo.
        Endpoint        NVARCHAR(2048) NOT NULL,

        -- Hash del endpoint para poder tener un índice ÚNICO sobre él: sin esto
        -- un mismo dispositivo suscrito dos veces recibe cada aviso dos veces.
        -- SHA2_256 necesita SQL 2012+ y esto corre en 2014.
        EndpointHash    AS (CONVERT(BINARY(32), HASHBYTES('SHA2_256', Endpoint))) PERSISTED,

        P256dhKey       NVARCHAR(512)  NOT NULL,
        AuthKey         NVARCHAR(512)  NOT NULL,

        -- 'iOS' | 'Android' | 'Escritorio'... Solo informativo: para que el
        -- panel diga "3 iPhone, 1 PC" en vez de un número pelado.
        Plataforma      NVARCHAR(20)   NULL,

        Creado          DATETIME       NOT NULL DEFAULT GETDATE(),
        UltimoUso       DATETIME       NULL,
        Activo          BIT            NOT NULL DEFAULT 1,

        CONSTRAINT PK_HUB_PushSuscripciones PRIMARY KEY (Id),
        CONSTRAINT FK_PushSuscripciones_Usuario
            FOREIGN KEY (IdUsuario) REFERENCES dbo.HUB_Users(Id) ON DELETE CASCADE
    )
    PRINT 'HUB_PushSuscripciones creada'
END
ELSE
    PRINT 'HUB_PushSuscripciones ya existe'
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes
               WHERE name = 'UX_HUB_PushSuscripciones_EndpointHash'
                 AND object_id = OBJECT_ID('dbo.HUB_PushSuscripciones'))
BEGIN
    CREATE UNIQUE INDEX UX_HUB_PushSuscripciones_EndpointHash
        ON dbo.HUB_PushSuscripciones (EndpointHash)
    PRINT 'indice unico por endpoint'
END
GO

-- El dispatcher pregunta siempre "¿quién tiene el permiso X y está suscrito
-- para la app Y?", así que el índice es por (App, IdUsuario).
IF NOT EXISTS (SELECT 1 FROM sys.indexes
               WHERE name = 'IX_HUB_PushSuscripciones_App_Usuario'
                 AND object_id = OBJECT_ID('dbo.HUB_PushSuscripciones'))
BEGIN
    CREATE INDEX IX_HUB_PushSuscripciones_App_Usuario
        ON dbo.HUB_PushSuscripciones (App, IdUsuario, Activo)
END
GO

-- ─── 2) Cola interna de avisos ────────────────────────────────────────────────
-- Sin esto no hay horario ni resumen. Los requisitos:
--   · fuera de lunes a viernes 09:00-18:30 TODOS los avisos se apagan y se
--     acumulan, para salir juntos al siguiente día laboral (un solo resumen);
--   · un resumen por vez ("Tienes 8 avisos desde el viernes"), no 8 avisos.
-- Eso obliga a GUARDAR lo pendiente, no solo a decidir enviar o no.
--
-- Deliberadamente NO tiene interfaz ni endpoint: el usuario pidió que en Admon
-- no haya historial ni bandeja. Es estado interno del worker; se limpia a los 30
-- días una vez entregado.
IF OBJECT_ID('dbo.HUB_AvisosCola', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.HUB_AvisosCola (
        Id              INT IDENTITY(1,1) NOT NULL,

        App             NVARCHAR(20)   NOT NULL,
        Tipo            NVARCHAR(40)   NOT NULL,

        IdUsuario       INT            NOT NULL,

        -- 'PENDIENTE' | 'ENVIADO' | 'FALLADO'. El dispatcher solo toma
        -- PENDIENTE; FALLADO no se reintenta a mano para no duplicar.
        Estado          NVARCHAR(12)   NOT NULL
                        CONSTRAINT DF_HUB_AvisosCola_Estado DEFAULT 'PENDIENTE',

        -- Marca el resumen: las filas del digest se agrupan por IdUsuario para
        -- salir en UNA sola notificación.
        EsResumen       BIT            NOT NULL DEFAULT 0,

        Titulo          NVARCHAR(200)  NOT NULL,
        Mensaje         NVARCHAR(1000) NOT NULL,
        Url             NVARCHAR(200)  NULL,

        Intentos        INT            NOT NULL DEFAULT 0,
        Creado          DATETIME       NOT NULL DEFAULT GETDATE(),
        Enviado         DATETIME       NULL,
        Detalle         NVARCHAR(400)  NULL,

        CONSTRAINT PK_HUB_AvisosCola PRIMARY KEY (Id),
        CONSTRAINT FK_HUB_AvisosCola_Usuario
            FOREIGN KEY (IdUsuario) REFERENCES dbo.HUB_Users(Id) ON DELETE CASCADE
    )
    PRINT 'HUB_AvisosCola creada'
END
ELSE
    PRINT 'HUB_AvisosCola ya existe'
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes
               WHERE name = 'IX_HUB_AvisosCola_Despacho'
                 AND object_id = OBJECT_ID('dbo.HUB_AvisosCola'))
BEGIN
    -- El barrido del dispatcher es: PENDIENTE de esta app, ordenadas por fecha.
    CREATE INDEX IX_HUB_AvisosCola_Despacho
        ON dbo.HUB_AvisosCola (App, Estado, Creado)
END
GO

-- Una fila por usuario y tipo dentro de la cola: es lo que impide que un mismo
-- evento se encole dos veces si el worker se reinicia a mitad de ciclo.
IF NOT EXISTS (SELECT 1 FROM sys.indexes
               WHERE name = 'UX_HUB_AvisosCola_Usuario_Tipo_Creado'
                 AND object_id = OBJECT_ID('dbo.HUB_AvisosCola'))
BEGIN
    CREATE UNIQUE INDEX UX_HUB_AvisosCola_Usuario_Tipo_Creado
        ON dbo.HUB_AvisosCola (IdUsuario, Tipo, Creado)
END
GO

-- ─── 3) Configuración de los avisos, en HUB_Config ────────────────────────────
-- Los interruptores NO viven en una tabla nueva: viven en HUB_Config como
-- cualquier otro ajuste, y acá solo se registra su metadata en el catálogo para
-- que aparezcan en la pestaña "⚙️ Apps" y se puedan editar desde ahí.
--
-- Se siembran ENCENDIDOS para los cinco: el sistema es nuevo y silencioso hasta
-- que alguien suscriba, así que encenderlos no genera ruido por sí solo.
MERGE dbo.HUB_ConfigCatalogo AS t
USING (VALUES
    ('admon', 'avisos_push_reporte_firmado',  'Aviso: reporte firmado',
     'Push a quien tenga permiso de Reportes cuando se firma un reporte.',
     'bool', NULL, 30),
    ('admon', 'avisos_push_kilometros',        'Aviso: kilometraje registrado',
     'Un aviso por vehiculo a la semana, a quien tenga permiso de Kilometros.',
     'bool', NULL, 31),
    ('admon', 'avisos_push_ticket_oxxogas',    'Aviso: ticket OxxoGas',
     'Push a quien tenga permiso de Vales OxxoGas cuando se registra un ticket.',
     'bool', NULL, 32),
    ('admon', 'avisos_push_cotizacion_firmada','Aviso: cotizacion firmada',
     'Push a quien tenga permiso de Cotizaciones cuando la cotizacion queda firmada.',
     'bool', NULL, 33),
    ('admon', 'avisos_push_cotizacion_facturada','Aviso: cotizacion facturada',
     'Push a quien tenga permiso de Cotizaciones cuando la cotizacion pasa a facturada.',
     'bool', NULL, 34),
    ('admon', 'avisos_push_horario_inicio',    'Avisos: hora de inicio',
     'Hora local en que empiezan a salir los avisos. Decimal: 9.5 = 09:30.',
     'number', 'h', 35),
    ('admon', 'avisos_push_horario_fin',       'Avisos: hora de fin',
     'Hora local en que dejan de salir los avisos. Decimal: 18.5 = 18:30.',
     'number', 'h', 36),
    ('admon', 'avisos_push_resumen_activo',    'Avisos: resumen unico',
     'Junar los avisos acumulados en una sola notificacion al dia siguiente.',
     'bool', NULL, 37)
) AS s(App, Clave, Titulo, Descripcion, Tipo, Unidad, Orden)
    ON t.App = s.App AND t.Clave = s.Clave
WHEN NOT MATCHED THEN
    INSERT (App, Clave, Titulo, Descripcion, Tipo, Unidad, Orden)
    VALUES (s.App, s.Clave, s.Titulo, s.Descripcion, s.Tipo, s.Unidad, s.Orden);
GO

-- Valores por defecto. El MERGE es UPDATE-then-INSERT porque SQL Server 2014 no
-- tiene ON CONFLICT y solo aplica al sembrar: si la clave ya existe NO se toca
-- (si no, cada re-aplicación del runner borraría la elección del usuario).
-- El fin de jornada va como hora decimal porque el horario acordado es 18:30 y
-- un entero solo podría decir 18:00.
MERGE dbo.HUB_Config AS t
USING (VALUES
    ('avisos_push_reporte_firmado',    '1'),
    ('avisos_push_kilometros',          '1'),
    ('avisos_push_ticket_oxxogas',      '1'),
    ('avisos_push_cotizacion_firmada',  '1'),
    ('avisos_push_cotizacion_facturada','1'),
    ('avisos_push_horario_inicio',      '9'),
    ('avisos_push_horario_fin',         '18.5'),
    ('avisos_push_resumen_activo',      '1')
) AS s(Clave, Valor)
    ON t.Clave = s.Clave
WHEN NOT MATCHED THEN
    INSERT (Clave, Valor) VALUES (s.Clave, s.Valor);
GO

PRINT '0056_avisos_push.sql aplicada'
GO
