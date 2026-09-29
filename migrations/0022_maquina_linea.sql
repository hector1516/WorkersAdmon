-- Migracion 0022: Agregar campo MaquinaLinea a ReportesServicio
-- Fecha: 2026-08-27
-- Descripcion: Agrega campo Maquina/Línea a reportes de servicio.
--              Se copia el contenido existente de Notas a MaquinaLinea para registros existentes.

IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('ReportesServicio') AND name = 'MaquinaLinea')
BEGIN
    ALTER TABLE dbo.ReportesServicio ADD MaquinaLinea varchar(200) NULL;
END
GO

-- Copiar Notas existentes a MaquinaLinea (una sola vez, truncando a 200 chars)
UPDATE ReportesServicio SET MaquinaLinea = LEFT(Notas, 200) WHERE MaquinaLinea IS NULL;
GO
