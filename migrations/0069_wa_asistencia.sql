-- 0069 · Avisos de asistencia por WhatsApp
--
-- El worker `wa_asistencia` avisa al PROPIO empleado: entrada, ausencia, salida
-- y resumen del viernes. El horario sale de `HUB_AsistenciaUsuarioHorario` +
-- `HUB_AsistenciaHorarioDias`, y los escaneos de `HUB_NetworkScanResults`
-- cruzados con `HUB_NetworkDevices`.
--
--   HUB_WaAsistenciaAvisos -> un renglón por intento. La clave única
--                             (Tipo, IdUsuario, Fecha) impide mandar dos veces
--                             el mismo aviso el mismo día. Los fallos también
--                             se guardan (Ok = 0) para no reintentar en bucle.
--
--     Tipo    ENTRADA | AUSENCIA | AUSENCIA2 | SALIDA | RESUMEN
--
-- Idempotente: se puede ejecutar más de una vez.
IF OBJECT_ID('dbo.HUB_WaAsistenciaAvisos', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.HUB_WaAsistenciaAvisos (
        Id        INT IDENTITY(1,1) NOT NULL
            CONSTRAINT PK_HUB_WaAsistenciaAvisos PRIMARY KEY,
        Tipo      VARCHAR(20)  NOT NULL,
        IdUsuario INT          NOT NULL,
        Fecha     DATE         NOT NULL,
        Enviado   DATETIME     NOT NULL
            CONSTRAINT DF_HUB_WaAsistenciaAvisos_Enviado DEFAULT GETDATE(),
        Ok        BIT          NOT NULL
            CONSTRAINT DF_HUB_WaAsistenciaAvisos_Ok DEFAULT 0,
        Detalle   VARCHAR(200) NULL
    );
    PRINT 'HUB_WaAsistenciaAvisos creada';
END
ELSE
    PRINT 'HUB_WaAsistenciaAvisos ya existe';
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes
               WHERE name = 'UX_HUB_WaAsistenciaAvisos_Dia'
                 AND object_id = OBJECT_ID('dbo.HUB_WaAsistenciaAvisos'))
    CREATE UNIQUE NONCLUSTERED INDEX UX_HUB_WaAsistenciaAvisos_Dia
        ON dbo.HUB_WaAsistenciaAvisos (Tipo, IdUsuario, Fecha);
GO

-- Valores por defecto. El MERGE solo siembra: una clave existente no se toca.
MERGE dbo.HUB_Config AS t
USING (VALUES
    ('wa_asist_activo',         '1'),
    ('wa_asist_tolerancia_min', '10'),
    ('wa_asist_ausencia_min',   '150'),
    ('wa_asist_salida_gap_min', '30'),
    ('wa_asist_max_dia',        '3'),
    ('wa_asist_resumen',        '1')
) AS s(Clave, Valor)
    ON t.Clave = s.Clave
WHEN NOT MATCHED THEN
    INSERT (Clave, Valor) VALUES (s.Clave, s.Valor);
GO

PRINT '0069_wa_asistencia.sql aplicada'
GO
