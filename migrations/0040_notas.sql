-- Migración 0040: Notas del equipo (módulo Notas de Admon + panel en Dashboard)
-- Fecha: 2026-09-28
-- Descripción: Notas compartidas de la empresa. Cada nota guarda su autor
--              (IdUsuario) para mostrarlo en el módulo y en el Dashboard.

IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'HUB_Notas')
BEGIN
    CREATE TABLE HUB_Notas (
        Id INT IDENTITY(1,1) PRIMARY KEY,
        Titulo NVARCHAR(200) NOT NULL,
        Contenido NVARCHAR(MAX) NOT NULL,
        IdUsuario INT NOT NULL,
        FechaCreacion DATETIME NOT NULL DEFAULT GETDATE(),
        FechaActualizado DATETIME NULL,
        FOREIGN KEY (IdUsuario) REFERENCES HUB_Users(Id)
    );
    PRINT 'Tabla HUB_Notas creada';
END
ELSE
BEGIN
    PRINT 'Tabla HUB_Notas ya existe';
END
