-- ============================================================================
-- 0036_config_catalogo.sql
-- Catálogo de claves de configuración POR APLICACIÓN (Fase D del panel
-- WorkersAdmon: pestaña "⚙️ Apps").
--
--   HUB_ConfigCatalogo → METADATOS de cada clave: a qué app pertenece, título,
--                        descripción, tipo (text/secret/number/bool/readonly),
--                        unidad, orden y posición en el grupo.
--   HUB_Config         → el VALOR sigue viviendo aquí (Clave/Valor), tal cual
--                        lo leen HUB, Field, admon y los workers. No se mueve
--                        nada: esta migración es puramente aditiva.
--
-- La pestaña Apps lee ambas tablas (LEFT JOIN por Clave). Las claves que ya
-- existen en HUB_Config pero todavía no están en el catálogo aparecen en el
-- grupo "Sin clasificar" para poder agruparlas desde la interfaz.
--
-- Idempotente: se puede ejecutar más de una vez (IF NOT EXISTS + WHERE NOT
-- EXISTS). Ejecutar primero en ECCSA_Admon_Pruebas con apply_migrations.py.
-- ============================================================================

IF OBJECT_ID('dbo.HUB_ConfigCatalogo', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.HUB_ConfigCatalogo (
        Id          INT           IDENTITY(1,1) NOT NULL,
        App         NVARCHAR(40)  NOT NULL,   -- HUB | Field | admon | <futura>
        Clave       NVARCHAR(50)  NOT NULL,   -- = HUB_Config.Clave
        Titulo      NVARCHAR(120) NOT NULL,
        Descripcion NVARCHAR(400) NULL,
        Tipo        NVARCHAR(20)  NOT NULL
                    CONSTRAINT DF_HUB_ConfigCatalogo_Tipo DEFAULT 'text',
        Unidad      NVARCHAR(20)  NULL,       -- seg | min | imgs | ...
        Orden       INT           NOT NULL
                    CONSTRAINT DF_HUB_ConfigCatalogo_Orden DEFAULT 0,
        Actualizado DATETIME      NOT NULL DEFAULT GETDATE(),
        CONSTRAINT PK_HUB_ConfigCatalogo PRIMARY KEY (Id),
        CONSTRAINT UQ_HUB_ConfigCatalogo_AppClave UNIQUE (App, Clave),
        CONSTRAINT CK_HUB_ConfigCatalogo_Tipo CHECK
            (Tipo IN ('text', 'secret', 'number', 'bool', 'readonly'))
    );

    CREATE NONCLUSTERED INDEX IX_HUB_ConfigCatalogo_App
        ON dbo.HUB_ConfigCatalogo (App, Orden);
END
GO

-- ─── Semilla: claves que ya se usan en producción ───────────────────────────
-- Si una fila ya existe (App + Clave) se conserva la que está en BD.
;WITH src AS (
    SELECT * FROM (VALUES
        -- ── HUB ────────────────────────────────────────────────────────────
        ('HUB', 'tipo_cambio_usd',        'Tipo de cambio USD/MXN',
         'Ultimo dolar FIX publicado; lo escribe el worker tipo_cambio_worker cada manana.',
         'readonly', NULL, 10),
        ('HUB', 'banxico_token',          'Token Banxico',
         'Serie SF51158 (dolar FIX). Vacia => el worker usa open.er-api.com.',
         'secret', NULL, 20),
        ('HUB', 'telegram_bot_token',     'Token del bot de Telegram',
         'De @BotFather; lo usan las alertas del HUB y telegram_worker.',
         'secret', NULL, 30),
        ('HUB', 'govale_user',            'Usuario GoVale',
         'Cuenta del portal Go Vale (vales y vouchers).',
         'text', NULL, 40),
        ('HUB', 'govale_password',        'Contrasena GoVale',
         'Misma cuenta para vales_worker y govale_vouchers_worker.',
         'secret', NULL, 41),
        ('HUB', 'govale_last_sync',       'Ultima sincronizacion GoVale',
         'La escribe govale_vouchers_worker; solo lectura.',
         'readonly', NULL, 42),
        ('HUB', 'govale_saldo',           'Saldo GoVale',
         'Saldo reportado por el portal; solo lectura.',
         'readonly', NULL, 43),
        ('HUB', 'govale_saldo_fecha',     'Fecha del saldo GoVale',
         'Solo lectura.', 'readonly', NULL, 44),
        ('HUB', 'govale_notif_fecha',     'Ultima notificacion de saldo',
         'Solo lectura.', 'readonly', NULL, 45),
        ('HUB', 'net_scan_enabled',       'Escaneo de red activo',
         'Apagado => network_scanner_worker no escanea la subred.',
         'bool', NULL, 50),
        ('HUB', 'net_scan_subnet',        'Subred a escanear',
         'Rango CIDR del inventario de IPs.',
         'text', NULL, 51),
        ('HUB', 'net_scan_interval_seg',  'Intervalo de escaneo',
         'Cadencia del escaneo ARP.',
         'number', 'seg', 52),
        ('HUB', 'net_tolerance_entrada_min', 'Tolerancia de entrada',
         'Escaneos consecutivos antes de marcar una entrada.',
         'number', 'min', 53),
        ('HUB', 'net_tolerance_salida_min',  'Tolerancia de salida',
         'Escaneos consecutivos antes de marcar una salida.',
         'number', 'min', 54),
        ('HUB', 'zerotier_enabled',       'ZeroTier activo',
         'Marca las IPs ZeroTier para ignorarlas en los reportes.',
         'bool', NULL, 60),
        ('HUB', 'zerotier_subnet',        'Subred ZeroTier',
         'Prefijo de la red ZeroTier (p. ej. 10.147.).',
         'text', NULL, 61),
        ('HUB', 'pdf_output_dir',         'Carpeta de salida de PDFs',
         'Directorio destino dentro del recurso SMB.',
         'text', NULL, 70),
        ('HUB', 'pdf_worker_last_run',    'Ultimo respaldo de PDFs',
         'Lo escribe pdf_storage_worker; solo lectura.',
         'readonly', NULL, 71),
        ('HUB', 'smb_share_path',         'Recurso SMB',
         'Ruta del Fileserver donde se respaldan los PDFs.',
         'text', NULL, 72),
        ('HUB', 'smb_user',               'Usuario SMB',
         'Credencial usada por pdf_storage (smbclient).',
         'text', NULL, 73),
        ('HUB', 'smb_password',           'Contrasena SMB',
         'Credencial usada por pdf_storage (smbclient).',
         'secret', NULL, 74),
        ('HUB', 'smb_domain',             'Dominio SMB',
         'Dominio del recurso compartido.',
         'text', NULL, 75),
        ('HUB', 'vapid_public_key',       'Llave publica VAPID',
         'Push web; la comparten el HUB y Field.',
         'text', NULL, 80),
        ('HUB', 'vapid_private_key',      'Llave privada VAPID',
         'Push web; solo lectura desde aqui salvo que la cambies.',
         'secret', NULL, 81),
        -- ── Field ──────────────────────────────────────────────────────────
        ('Field', 'field_avisos_rep_1',   'Aviso de reporte Field #1',
         'Reportes que dispara el aviso automatico de Field.', 'text', NULL, 10),
        ('Field', 'field_avisos_rep_2',   'Aviso de reporte Field #2',
         'Reportes que dispara el aviso automatico de Field.', 'text', NULL, 11),
        ('Field', 'field_avisos_rep_3',   'Aviso de reporte Field #3',
         'Reportes que dispara el aviso automatico de Field.', 'text', NULL, 12),
        ('Field', 'field_avisos_rep_4',   'Aviso de reporte Field #4',
         'Reportes que dispara el aviso automatico de Field.', 'text', NULL, 13),
        ('Field', 'field_avisos_rep_5',   'Aviso de reporte Field #5',
         'Reportes que dispara el aviso automatico de Field.', 'text', NULL, 14),
        ('Field', 'field_avisos_rep_6',   'Aviso de reporte Field #6',
         'Reportes que dispara el aviso automatico de Field.', 'text', NULL, 15),
        ('Field', 'field_avisos_rep_7',   'Aviso de reporte Field #7',
         'Reportes que dispara el aviso automatico de Field.', 'text', NULL, 16),
        ('Field', 'field_avisos_rep_8',   'Aviso de reporte Field #8',
         'Reportes que dispara el aviso automatico de Field.', 'text', NULL, 17),
        ('Field', 'field_avisos_rep_9',   'Aviso de reporte Field #9',
         'Reportes que dispara el aviso automatico de Field.', 'text', NULL, 18),
        ('Field', 'field_avisos_rep_10',  'Aviso de reporte Field #10',
         'Reportes que dispara el aviso automatico de Field.', 'text', NULL, 19),
        ('Field', 'field_avisos_rep_11',  'Aviso de reporte Field #11',
         'Reportes que dispara el aviso automatico de Field.', 'text', NULL, 20),
        -- ── admon ──────────────────────────────────────────────────────────
        ('admon', 'admon_passkey_secret', 'Secreto de passkeys (admon)',
         'Firma de los tokens de passkey del portal admon.',
         'secret', NULL, 10),
        ('admon', 'asistencia_calcular_auto', 'Asistencia: calculo automatico',
         'Cierra y calcula la asistencia sin captura manual.',
         'bool', NULL, 20),
        ('admon', 'asistencia_tol_llegada_min', 'Asistencia: tolerancia de llegada',
         'Minutos extras antes de marcar atraso.',
         'number', 'min', 21),
        ('admon', 'asistencia_tol_salida_antes_min', 'Asistencia: tolerancia de salida',
         'Minutos antes del horario en que se permite la salida.',
         'number', 'min', 22)
    ) AS v(App, Clave, Titulo, Descripcion, Tipo, Unidad, Orden)
)
INSERT INTO dbo.HUB_ConfigCatalogo (App, Clave, Titulo, Descripcion, Tipo, Unidad, Orden)
SELECT s.App, s.Clave, s.Titulo, s.Descripcion, s.Tipo, s.Unidad, s.Orden
FROM src s
WHERE NOT EXISTS (
    SELECT 1 FROM dbo.HUB_ConfigCatalogo c
    WHERE c.App = s.App AND c.Clave = s.Clave
);
GO
