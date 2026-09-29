-- 0001_schema_migrations.sql
-- Tabla de control: registra qué archivos de /migrations ya se aplicaron a esta base.
-- Se crea UNA vez por base de datos (pruebas y producción). No depende de otra cosa.

IF OBJECT_ID('dbo.schema_migrations', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.schema_migrations (
        id            INT IDENTITY(1,1) PRIMARY KEY,
        version       NVARCHAR(255) NOT NULL UNIQUE,
        created_at    DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
        applied_by    NVARCHAR(255) NULL
    );
END
GO