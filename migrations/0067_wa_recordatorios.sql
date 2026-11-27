-- 0067 · Recordatorios proactivos del bot de WhatsApp (fase C)
--
-- El bot ya contesta lo que le preguntan (`wa_bot`, `wa_consultas`). El worker
-- `wa_recordatorios` es el otro sentido: avisa lo que FALTA (kilómetros sin
-- registrar, vale sin comprobante) y manda el resumen del lunes.
--
--   HUB_WaRecordatorios -> un renglón por intento de aviso. La clave única es
--                          (Tipo, Clave, IdUsuario, FechaDia): el mismo aviso no
--                          se manda dos veces el mismo día. Los fallos también
--                          se guardan (Ok = 0) para no reintentar en bucle; el
--                          tope diario cuenta sólo lo enviado (Ok = 1).
--
--     Tipo    KM | VALE | RESUMEN
--     Clave   id del auto / id del vale / 'lunes-YYYY-MM-DD'
--
-- Idempotente: se puede ejecutar más de una vez.
IF OBJECT_ID('dbo.HUB_WaRecordatorios', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.HUB_WaRecordatorios (
        Id        INT IDENTITY(1,1) NOT NULL
            CONSTRAINT PK_HUB_WaRecordatorios PRIMARY KEY,
        Tipo      VARCHAR(20)  NOT NULL,
        Clave     VARCHAR(60)  NOT NULL,
        IdUsuario INT          NOT NULL,
        FechaDia  DATE         NOT NULL,
        Enviado   DATETIME     NOT NULL
            CONSTRAINT DF_HUB_WaRecordatorios_Enviado DEFAULT GETDATE(),
        Ok        BIT          NOT NULL
            CONSTRAINT DF_HUB_WaRecordatorios_Ok DEFAULT 0,
        Detalle   VARCHAR(200) NULL
    );
    PRINT 'HUB_WaRecordatorios creada';
END
ELSE
    PRINT 'HUB_WaRecordatorios ya existe';
GO

-- La garantía final del dedupe: dos entregas del mismo turno no pueden dejar
-- dos renglones iguales.
IF NOT EXISTS (SELECT 1 FROM sys.indexes
               WHERE name = 'UX_HUB_WaRecordatorios_Dia'
                 AND object_id = OBJECT_ID('dbo.HUB_WaRecordatorios'))
    CREATE UNIQUE NONCLUSTERED INDEX UX_HUB_WaRecordatorios_Dia
        ON dbo.HUB_WaRecordatorios (Tipo, Clave, IdUsuario, FechaDia);
GO

-- Valores por defecto. El MERGE solo siembra: si la clave ya existe NO se toca
-- (si no, cada re-aplicación borraría la elección del usuario).
-- `Valor` admite hasta 500 caracteres.
MERGE dbo.HUB_Config AS t
USING (VALUES
    ('wa_rec_activo',      '1'),
    ('wa_rec_km_dias',     '3'),
    ('wa_rec_vale_horas',  '48'),
    ('wa_rec_vale_max',    '3'),
    ('wa_rec_resumen',     '1'),
    ('wa_rec_max_dia',     '3')
) AS s(Clave, Valor)
    ON t.Clave = s.Clave
WHEN NOT MATCHED THEN
    INSERT (Clave, Valor) VALUES (s.Clave, s.Valor);
GO

PRINT '0067_wa_recordatorios.sql aplicada'
GO
