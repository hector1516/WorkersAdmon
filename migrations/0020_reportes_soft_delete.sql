-- Migración 0020: Agregar soft delete (papelera) a ReportesServicio
-- Fecha: 2026-09-24
-- Descripción: Agrega columnas Eliminado y FechaEliminado para papelera

-- Verificar si las columnas ya existen
IF NOT EXISTS (SELECT * FROM sys.columns WHERE object_id = OBJECT_ID('ReportesServicio') AND name = 'Eliminado')
BEGIN
    ALTER TABLE ReportesServicio ADD Eliminado BIT NOT NULL DEFAULT 0;
    PRINT 'Columna Eliminado agregada';
END
ELSE
BEGIN
    PRINT 'Columna Eliminado ya existe';
END

IF NOT EXISTS (SELECT * FROM sys.columns WHERE object_id = OBJECT_ID('ReportesServicio') AND name = 'FechaEliminado')
BEGIN
    ALTER TABLE ReportesServicio ADD FechaEliminado DATETIME NULL;
    PRINT 'Columna FechaEliminado agregada';
END
ELSE
BEGIN
    PRINT 'Columna FechaEliminado ya existe';
END

-- Crear índice para filtrar rápido
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID('ReportesServicio') AND name = 'IX_ReportesServicio_Eliminado')
BEGIN
    CREATE INDEX IX_ReportesServicio_Eliminado ON ReportesServicio (Eliminado);
    PRINT 'Índice IX_ReportesServicio_Eliminado creado';
END
ELSE
BEGIN
    PRINT 'Índice IX_ReportesServicio_Eliminado ya existe';
END