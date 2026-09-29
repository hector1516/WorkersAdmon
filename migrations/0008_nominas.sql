-- 0008_nominas.sql
-- Migración: módulo de Nóminas (confidencial).
-- 1) Columna de permiso en HUB_Users.
-- 2) Tablas: semanas, registros por empleado, préstamos, utilidades, aguinaldo y bitácora interna.

IF COL_LENGTH('dbo.HUB_Users', 'AccesoNominas') IS NULL
BEGIN
    ALTER TABLE dbo.HUB_Users ADD AccesoNominas BIT NOT NULL DEFAULT 0;
END
GO

IF OBJECT_ID('dbo.HUB_NominaSemana', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.HUB_NominaSemana (
        Id INT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        NumeroSemana INT NOT NULL,
        FechaSemana DATE NOT NULL,
        Notas NVARCHAR(500) NULL,
        CreadoPor INT NULL,
        FechaCreacion DATETIME NOT NULL DEFAULT GETDATE()
    );
END
GO

IF OBJECT_ID('dbo.HUB_NominaRegistro', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.HUB_NominaRegistro (
        Id INT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        IdSemana INT NOT NULL REFERENCES dbo.HUB_NominaSemana(Id),
        IdUsuario INT NOT NULL REFERENCES dbo.HUB_Users(Id),
        Sueldo DECIMAL(12,2) NOT NULL DEFAULT 0,
        Efectivo DECIMAL(12,2) NOT NULL DEFAULT 0,
        Recibo DECIMAL(12,2) NOT NULL DEFAULT 0,
        Nota NVARCHAR(500) NULL,
        CONSTRAINT UQ_NominaRegistro_Semana_Usuario UNIQUE (IdSemana, IdUsuario)
    );
END
GO

IF OBJECT_ID('dbo.HUB_NominaPrestamo', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.HUB_NominaPrestamo (
        Id INT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        IdUsuario INT NOT NULL REFERENCES dbo.HUB_Users(Id),
        Monto DECIMAL(12,2) NOT NULL DEFAULT 0,
        PagoSemanal DECIMAL(12,2) NOT NULL DEFAULT 0,
        PagosTotales INT NOT NULL DEFAULT 0,
        PagosRealizados INT NOT NULL DEFAULT 0,
        FechaInicio DATE NULL,
        Nota NVARCHAR(500) NULL,
        Activo BIT NOT NULL DEFAULT 1,
        FechaCreacion DATETIME NOT NULL DEFAULT GETDATE()
    );
END
GO

IF OBJECT_ID('dbo.HUB_NominaUtilidad', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.HUB_NominaUtilidad (
        Id INT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        IdUsuario INT NOT NULL REFERENCES dbo.HUB_Users(Id),
        Anio INT NOT NULL,
        Monto DECIMAL(12,2) NOT NULL DEFAULT 0,
        Nota NVARCHAR(500) NULL,
        FechaCreacion DATETIME NOT NULL DEFAULT GETDATE(),
        CONSTRAINT UQ_NominaUtilidad_Usuario_Anio UNIQUE (IdUsuario, Anio)
    );
END
GO

IF OBJECT_ID('dbo.HUB_NominaAguinaldo', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.HUB_NominaAguinaldo (
        Id INT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        IdUsuario INT NOT NULL REFERENCES dbo.HUB_Users(Id),
        Anio INT NOT NULL,
        Monto DECIMAL(12,2) NOT NULL DEFAULT 0,
        Nota NVARCHAR(500) NULL,
        FechaCreacion DATETIME NOT NULL DEFAULT GETDATE(),
        CONSTRAINT UQ_NominaAguinaldo_Usuario_Anio UNIQUE (IdUsuario, Anio)
    );
END
GO

IF OBJECT_ID('dbo.HUB_NominaBitacora', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.HUB_NominaBitacora (
        Id INT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        Usuario NVARCHAR(200) NOT NULL,
        Modulo NVARCHAR(100) NOT NULL,
        Accion NVARCHAR(1000) NOT NULL,
        FechaHora DATETIME NOT NULL DEFAULT GETDATE()
    );
END
GO
