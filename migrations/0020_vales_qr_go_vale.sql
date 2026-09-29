-- 0020_vales_qr_go_vale.sql
-- Módulo Vale Digital QR (Go Vale OxxoGas): solicitar, generar y ver vales QR.
-- Credenciales go-vale en HUB_Config, solicitudes en HUB_SolicitudVales.
-- Permisos: AccesoSolicitarVales (operativo), AccesoAdminVales (admin), AccesoConfigOxxogas (config).

-- 1) Tabla de solicitudes de vales QR
IF OBJECT_ID('dbo.HUB_SolicitudVales', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.HUB_SolicitudVales (
        Id              INT IDENTITY(1,1) PRIMARY KEY,
        IdSolicitante   INT NOT NULL,
        IdAprobador     INT NULL,
        FechaSolicitud  DATETIME NOT NULL DEFAULT GETDATE(),
        FechaAprobado   DATETIME NULL,
        Placa           NVARCHAR(20) NOT NULL,
        Descripcion     NVARCHAR(200) NOT NULL,
        Cantidad        INT NOT NULL DEFAULT 1,
        MontoUnit       DECIMAL(10,2) NOT NULL DEFAULT 500.00,
        Estatus         VARCHAR(20) NOT NULL DEFAULT 'PENDIENTE',
        IdValeGoVale    VARCHAR(50) NULL,
        CodigoQR        NVARCHAR(2000) NULL,
        UrlQR           NVARCHAR(500) NULL,
        Notas           NVARCHAR(500) NULL,
        CONSTRAINT FK_SolicitudVales_Solicitante FOREIGN KEY (IdSolicitante) REFERENCES dbo.HUB_Users(Id),
        CONSTRAINT FK_SolicitudVales_Aprobador   FOREIGN KEY (IdAprobador)   REFERENCES dbo.HUB_Users(Id)
    );

    CREATE INDEX IX_SolicitudVales_Estatus ON dbo.HUB_SolicitudVales(Estatus, FechaSolicitud DESC);
    CREATE INDEX IX_SolicitudVales_Solicitante ON dbo.HUB_SolicitudVales(IdSolicitante, FechaSolicitud DESC);
END
GO

-- 2) Credenciales Go Vale en HUB_Config (valores vacíos, se configuran desde la vista)
IF NOT EXISTS (SELECT 1 FROM dbo.HUB_Config WHERE Clave = 'govale_user')
    INSERT INTO dbo.HUB_Config (Clave, Valor) VALUES ('govale_user', '');
GO
IF NOT EXISTS (SELECT 1 FROM dbo.HUB_Config WHERE Clave = 'govale_password')
    INSERT INTO dbo.HUB_Config (Clave, Valor) VALUES ('govale_password', '');
GO

-- 3) Columnas de permiso en HUB_Users
IF COL_LENGTH('dbo.HUB_Users', 'AccesoSolicitarVales') IS NULL
    ALTER TABLE dbo.HUB_Users ADD AccesoSolicitarVales BIT NOT NULL DEFAULT 0;
GO
IF COL_LENGTH('dbo.HUB_Users', 'AccesoAdminVales') IS NULL
    ALTER TABLE dbo.HUB_Users ADD AccesoAdminVales BIT NOT NULL DEFAULT 0;
GO
IF COL_LENGTH('dbo.HUB_Users', 'AccesoConfigOxxogas') IS NULL
    ALTER TABLE dbo.HUB_Users ADD AccesoConfigOxxogas BIT NOT NULL DEFAULT 0;
GO
