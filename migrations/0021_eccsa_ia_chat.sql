-- Migración 0021: ECCSA IA - Historial de conversaciones
-- Fecha: 2026-09-25
-- Descripción: Tablas para chat IA con limpieza automática a 7 días

-- Tabla de conversaciones (sesiones de chat)
IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'HUB_IaConversaciones')
BEGIN
    CREATE TABLE HUB_IaConversaciones (
        IdConversacion INT IDENTITY(1,1) PRIMARY KEY,
        IdUsuario INT NOT NULL,
        Titulo NVARCHAR(200) DEFAULT 'Nueva conversación',
        FechaCreacion DATETIME NOT NULL DEFAULT GETDATE(),
        FechaActualizacion DATETIME NOT NULL DEFAULT GETDATE(),
        Activo BIT NOT NULL DEFAULT 1,
        FOREIGN KEY (IdUsuario) REFERENCES HUB_Users(Id)
    );
    PRINT 'Tabla HUB_IaConversaciones creada';
END
ELSE
BEGIN
    PRINT 'Tabla HUB_IaConversaciones ya existe';
END

-- Tabla de mensajes
IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'HUB_IaMensajes')
BEGIN
    CREATE TABLE HUB_IaMensajes (
        IdMensaje INT IDENTITY(1,1) PRIMARY KEY,
        IdConversacion INT NOT NULL,
        Rol NVARCHAR(20) NOT NULL CHECK (Rol IN ('user', 'assistant', 'system')),
        Contenido NVARCHAR(MAX) NOT NULL,
        TokensEntrada INT NULL,
        TokensSalida INT NULL,
        Modelo NVARCHAR(100) NULL,
        FechaCreacion DATETIME NOT NULL DEFAULT GETDATE(),
        FOREIGN KEY (IdConversacion) REFERENCES HUB_IaConversaciones(IdConversacion) ON DELETE CASCADE
    );
    PRINT 'Tabla HUB_IaMensajes creada';
END
ELSE
BEGIN
    PRINT 'Tabla HUB_IaMensajes ya existe';
END

-- Índices
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'IX_HUB_IaConversaciones_Usuario_Fecha')
BEGIN
    CREATE INDEX IX_HUB_IaConversaciones_Usuario_Fecha ON HUB_IaConversaciones (IdUsuario, FechaActualizacion DESC);
    PRINT 'Índice IX_HUB_IaConversaciones_Usuario_Fecha creado';
END

IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'IX_HUB_IaMensajes_Conversacion_Fecha')
BEGIN
    CREATE INDEX IX_HUB_IaMensajes_Conversacion_Fecha ON HUB_IaMensajes (IdConversacion, FechaCreacion);
    PRINT 'Índice IX_HUB_IaMensajes_Conversacion_Fecha creado';
END

-- Job/Procedimiento para limpieza de conversaciones > 7 días
-- Se ejecutará via cron job o manualmente