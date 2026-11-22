-- 0064 · Log universal de workers (en la base, consultable desde el panel)
--
-- Por qué: cada worker escribe en /var/log/supervisor/<worker>.log, dentro del
-- contenedor. Eso no sale en el log de Docker y no se puede leer sin entrar al
-- servidor: en el incidente del 22-nov diagnosticamos a ciegas.
--
-- OJO con el volumen: 15 workers cada 15 s son ~86 000 filas/día. Por eso:
--   * sólo se registran eventos (arranque, trabajo tomado, resultado, error),
--     NUNCA cada vuelta del bucle;
--   * se poda a 7 días (worker_log.podar()).
IF OBJECT_ID('dbo.HUB_WorkersLog', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.HUB_WorkersLog (
        Id        BIGINT IDENTITY(1,1) NOT NULL
            CONSTRAINT PK_HUB_WorkersLog PRIMARY KEY,
        Worker    NVARCHAR(40)  NOT NULL,
        Nivel     NVARCHAR(10)  NOT NULL CONSTRAINT DF_WLog_Nivel DEFAULT ('INFO'),
        Mensaje   NVARCHAR(400) NOT NULL,
        Detalle   NVARCHAR(400) NULL,
        Creado    DATETIME      NOT NULL CONSTRAINT DF_WLog_Creado DEFAULT (GETDATE())
    );
    CREATE INDEX IX_WLog_Creado ON dbo.HUB_WorkersLog (Creado DESC);
    CREATE INDEX IX_WLog_Worker ON dbo.HUB_WorkersLog (Worker, Creado DESC);
    PRINT 'HUB_WorkersLog creada';
END
ELSE
    PRINT 'HUB_WorkersLog ya existe';
GO
