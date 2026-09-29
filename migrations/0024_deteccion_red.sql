-- 0024_deteccion_red.sql
-- Módulo de Detección de Dispositivos por Red (ARP scan).
-- Tablas: catálogo de dispositivos, resultados de scan, estado de presencia, historial.
-- Permisos: columna AccesoDeteccionRed en HUB_Users.
-- Config: claves en HUB_Config para tolerancias e intervalo.

-- 1) Tabla de dispositivos conocidos (catálogo MAC ↔ usuario)
IF OBJECT_ID('dbo.HUB_NetworkDevices', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.HUB_NetworkDevices (
        Id                  INT IDENTITY(1,1) PRIMARY KEY,
        MACAddress          VARCHAR(17) NOT NULL,
        NombreDispositivo   VARCHAR(100) NULL,
        IdUsuario           INT NULL,
        Tipo                VARCHAR(20) NOT NULL DEFAULT 'CELULAR',
        Activo              BIT NOT NULL DEFAULT 1,
        FechaRegistro       DATETIME NOT NULL DEFAULT GETDATE(),
        Notas               VARCHAR(500) NULL,
        CONSTRAINT UQ_NetworkDevices_MAC UNIQUE (MACAddress)
    );
    CREATE INDEX IX_NetworkDevices_Usuario ON dbo.HUB_NetworkDevices(IdUsuario);
END
GO

-- 2) Tabla de resultados de escaneo (una fila por dispositivo detectado en cada scan)
IF OBJECT_ID('dbo.HUB_NetworkScanResults', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.HUB_NetworkScanResults (
        Id          INT IDENTITY(1,1) PRIMARY KEY,
        MACAddress  VARCHAR(17) NOT NULL,
        IP          VARCHAR(15) NULL,
        Hostname    VARCHAR(200) NULL,
        FechaScan   DATETIME NOT NULL DEFAULT GETDATE(),
        Procesado   BIT NOT NULL DEFAULT 0
    );
    CREATE INDEX IX_NetworkScanResults_Fecha ON dbo.HUB_NetworkScanResults(FechaScan);
    CREATE INDEX IX_NetworkScanResults_Procesado ON dbo.HUB_NetworkScanResults(Procesado);
END
GO

-- 3) Tabla de estado actual por dispositivo (último conocido)
IF OBJECT_ID('dbo.HUB_NetworkState', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.HUB_NetworkState (
        IdDispositivo   INT NOT NULL PRIMARY KEY,
        Estado          VARCHAR(10) NOT NULL DEFAULT 'FUERA',
        UltimaVezEnRed  DATETIME NULL,
        UltimoScanOk    DATETIME NULL,
        CONSTRAINT FK_NetworkState_Device FOREIGN KEY (IdDispositivo)
            REFERENCES dbo.HUB_NetworkDevices(Id)
    );
END
GO

-- 4) Tabla de historial de presencia (eventos entrada/salida)
IF OBJECT_ID('dbo.HUB_NetworkPresence', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.HUB_NetworkPresence (
        Id              INT IDENTITY(1,1) PRIMARY KEY,
        IdDispositivo   INT NOT NULL,
        FechaHora       DATETIME NOT NULL DEFAULT GETDATE(),
        TipoEvento      VARCHAR(10) NOT NULL,
        Confianza       DECIMAL(5,2) NOT NULL DEFAULT 100.0,
        Notas           VARCHAR(200) NULL,
        CONSTRAINT FK_NetworkPresence_Device FOREIGN KEY (IdDispositivo)
            REFERENCES dbo.HUB_NetworkDevices(Id)
    );
    CREATE INDEX IX_NetworkPresence_Fecha ON dbo.HUB_NetworkPresence(FechaHora);
    CREATE INDEX IX_NetworkPresence_Dispositivo ON dbo.HUB_NetworkPresence(IdDispositivo);
END
GO

-- 5) Columna de permiso en HUB_Users
IF COL_LENGTH('dbo.HUB_Users', 'AccesoDeteccionRed') IS NULL
    ALTER TABLE dbo.HUB_Users ADD AccesoDeteccionRed BIT NOT NULL DEFAULT 0;
GO

-- 6) Config por defecto en HUB_Config (solo si no existen)
IF NOT EXISTS (SELECT 1 FROM dbo.HUB_Config WHERE Clave = 'net_scan_subnet')
    INSERT INTO dbo.HUB_Config (Clave, Valor) VALUES ('net_scan_subnet', '172.26.90.0/24');
GO
IF NOT EXISTS (SELECT 1 FROM dbo.HUB_Config WHERE Clave = 'net_scan_interval_seg')
    INSERT INTO dbo.HUB_Config (Clave, Valor) VALUES ('net_scan_interval_seg', '180');
GO
IF NOT EXISTS (SELECT 1 FROM dbo.HUB_Config WHERE Clave = 'net_tolerance_salida_min')
    INSERT INTO dbo.HUB_Config (Clave, Valor) VALUES ('net_tolerance_salida_min', '5');
GO
IF NOT EXISTS (SELECT 1 FROM dbo.HUB_Config WHERE Clave = 'net_tolerance_entrada_min')
    INSERT INTO dbo.HUB_Config (Clave, Valor) VALUES ('net_tolerance_entrada_min', '2');
GO
IF NOT EXISTS (SELECT 1 FROM dbo.HUB_Config WHERE Clave = 'net_scan_enabled')
    INSERT INTO dbo.HUB_Config (Clave, Valor) VALUES ('net_scan_enabled', '1');
GO
