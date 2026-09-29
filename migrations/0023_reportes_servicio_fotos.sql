-- Migracion 0023: Tabla de fotos para Reportes de Servicio
-- Fecha: 2026-09-01
-- Descripcion: Almacena hasta 6 fotos por reporte de servicio (evidencia fotográfica).
--              Fotos comprimidas en VARBINARY(MAX) para ahorrar espacio.

IF NOT EXISTS (SELECT 1 FROM sys.objects WHERE object_id = OBJECT_ID('ReportesServicioFotos') AND type = 'U')
BEGIN
    CREATE TABLE dbo.ReportesServicioFotos (
        IdFoto       INT NOT NULL IDENTITY,
        IdReporte    INT NOT NULL,
        FotoComprimida VARBINARY(MAX) NOT NULL,
        Orden        TINYINT NOT NULL DEFAULT 0,
        FechaSubida  DATETIME NOT NULL DEFAULT GETDATE(),
        CONSTRAINT PK_ReportesServicioFotos PRIMARY KEY CLUSTERED (IdFoto),
        CONSTRAINT FK_Foto_Reporte FOREIGN KEY (IdReporte) REFERENCES dbo.ReportesServicio(IdReporte) ON DELETE CASCADE
    );
END
GO
