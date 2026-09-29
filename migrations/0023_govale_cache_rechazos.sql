-- 0023_govale_cache_rechazos.sql
-- Tabla cache de vales Go Vale + columnas de rechazo en solicitudes.
-- El worker cron_sync_govale_vournals.py mantiene la tabla cache sincronizada.

-- 1) Tabla cache de vales Go Vale
IF OBJECT_ID('dbo.HUB_OxxoGas_VouchersCache', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.HUB_OxxoGas_VouchersCache (
        Id                  INT IDENTITY(1,1) PRIMARY KEY,
        QrCode              VARCHAR(100) NOT NULL,
        VoucherId           INT NULL,
        Monto               DECIMAL(10,2) NULL,
        Saldo               DECIMAL(10,2) NULL,
        Estatus             VARCHAR(50) NULL,
        Contacto            VARCHAR(100) NULL,
        Empresa             VARCHAR(100) NULL,
        QrImage             VARBINARY(MAX) NULL,
        FechaCreacionGoVale DATETIME NULL,
        FechaSincronizado   DATETIME NOT NULL DEFAULT GETDATE(),
        CONSTRAINT UQ_VouchersCache_QrCode UNIQUE (QrCode)
    );

    CREATE INDEX IX_VouchersCache_Contacto ON dbo.HUB_OxxoGas_VouchersCache(Contacto);
    CREATE INDEX IX_VouchersCache_Estatus ON dbo.HUB_OxxoGas_VouchersCache(Estatus);
END
GO

-- 2) Columnas nuevas en HUB_SolicitudVales
IF COL_LENGTH('dbo.HUB_SolicitudVales', 'QrImage') IS NULL
    ALTER TABLE dbo.HUB_SolicitudVales ADD QrImage VARBINARY(MAX);
GO
IF COL_LENGTH('dbo.HUB_SolicitudVales', 'MotivoRechazo') IS NULL
    ALTER TABLE dbo.HUB_SolicitudVales ADD MotivoRechazo NVARCHAR(500) NULL;
GO
IF COL_LENGTH('dbo.HUB_SolicitudVales', 'FechaRechazo') IS NULL
    ALTER TABLE dbo.HUB_SolicitudVales ADD FechaRechazo DATETIME NULL;
GO
IF COL_LENGTH('dbo.HUB_SolicitudVales', 'RechazadoPor') IS NULL
    ALTER TABLE dbo.HUB_SolicitudVales ADD RechazadoPor INT NULL;
GO

-- 3) Config: ultima sincronizacion de vales Go Vale
IF NOT EXISTS (SELECT 1 FROM dbo.HUB_Config WHERE Clave = 'govale_last_sync')
    INSERT INTO dbo.HUB_Config (Clave, Valor) VALUES ('govale_last_sync', '2026-08-31 00:00:00');
GO
