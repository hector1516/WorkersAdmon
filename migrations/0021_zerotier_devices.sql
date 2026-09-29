-- ZeroTier Device Management for Auto-Login
-- Ejecutar en BD de pruebas (ECCSA_Admon_Pruebas) y luego en producción

-- Tabla de dispositivos ZeroTier vinculados a usuarios
CREATE TABLE HUB_ZeroTierDevices (
    Id INT IDENTITY(1,1) PRIMARY KEY,
    ZeroTierIP VARCHAR(45) NOT NULL,          -- ej. '10.147.18.42'
    UserEmail VARCHAR(255) NOT NULL,          -- usuario dueño del dispositivo
    DeviceName VARCHAR(100) NULL,             -- nombre amigable "Laptop Héctor", "Celular Juan"
    RegisteredAt DATETIME DEFAULT GETDATE(),
    LastSeenAt DATETIME DEFAULT GETDATE(),
    IsActive BIT DEFAULT 1,
    UNIQUE (ZeroTierIP)
);
CREATE INDEX IX_HUB_ZeroTierDevices_UserEmail ON HUB_ZeroTierDevices(UserEmail);
CREATE INDEX IX_HUB_ZeroTierDevices_IsActive ON HUB_ZeroTierDevices(IsActive);

-- Configuración global ZeroTier en HUB_Config
INSERT INTO HUB_Config (Clave, Valor) VALUES 
('zerotier_subnet', '10.147.'),
('zerotier_enabled', '1');