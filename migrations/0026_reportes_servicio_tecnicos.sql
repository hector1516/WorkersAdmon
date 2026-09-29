-- Migracion 0026: Tecnicos adicionales en Reportes de Servicio
IF NOT EXISTS (SELECT 1 FROM sys.objects WHERE object_id = OBJECT_ID('ReportesServicioTecnicos') AND type = 'U')
BEGIN
    CREATE TABLE dbo.ReportesServicioTecnicos (
        IdReporte INT NOT NULL,
        IdUsuario INT NOT NULL,
        CONSTRAINT PK_ReportesServicioTecnicos PRIMARY KEY CLUSTERED (IdReporte, IdUsuario),
        CONSTRAINT FK_TecnicoAdicional_Reporte FOREIGN KEY (IdReporte) REFERENCES dbo.ReportesServicio(IdReporte) ON DELETE CASCADE,
        CONSTRAINT FK_TecnicoAdicional_Usuario FOREIGN KEY (IdUsuario) REFERENCES dbo.HUB_Users(Id)
    );
END
GO
