-- 0019_app_config.sql
-- Módulo AppConfig: configuración de frases de splash screen.
-- Tabla: HUB_SplashPhrases (frases de carga)
-- Columna permiso: AccesoAppConfig en HUB_Users

-- 1) Tabla de frases de splash screen
IF OBJECT_ID('dbo.HUB_SplashPhrases', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.HUB_SplashPhrases (
        Id              INT IDENTITY(1,1) PRIMARY KEY,
        Tipo            VARCHAR(30) NOT NULL DEFAULT 'SPLASH_MSG',
        Texto           NVARCHAR(500) NOT NULL,
        Activo          BIT NOT NULL DEFAULT 1,
        FechaCreacion   DATETIME NOT NULL DEFAULT GETDATE(),
        FechaActualizado DATETIME NULL
    );

    INSERT INTO dbo.HUB_SplashPhrases (Tipo, Texto, Activo)
    VALUES
        ('SPLASH_MSG', N'💻 Inicializando matriz de datos cuánticos...', 1),
        ('SPLASH_MSG', N'🚀 Calibrando condensadores de flujo a 1.21 gigavatios...', 1),
        ('SPLASH_MSG', N'🛡️ Bypass al cortafuegos del mainframe de la NASA...', 1),
        ('SPLASH_MSG', N'☕ Convirtiendo café en líneas de código...', 1),
        ('SPLASH_MSG', N'🤖 Entrenando a los duendes del servidor para pedalear más rápido...', 1),
        ('SPLASH_MSG', N'🛸 Estableciendo enlace seguro con la nave nodriza de ECCSA...', 1),
        ('SPLASH_MSG', N'🔑 Desencriptando algoritmos de acceso al HUB...', 1),
        ('SPLASH_MSG', N'🎮 Cargando la simulación (por favor, actúe normal)...', 1),
        ('SPLASH_MSG', N'💾 Cargando módulos del kernel a velocidad de disquete...', 1),
        ('SPLASH_MSG', N'🦄 Peinando unicornios de base de datos...', 1),
        ('SPLASH_MSG', N'🧮 Calculando el sentido de la vida, el universo y todo lo demás...', 1),
        ('SPLASH_MSG', N'⚡ Incrementando la entropía térmica del procesador...', 1),
        ('SPLASH_MSG', N'📡 Alineando satélites geosíncronos de ECCSA...', 1);
END
GO

-- 2) Columna de permiso en HUB_Users
IF COL_LENGTH('dbo.HUB_Users', 'AccesoAppConfig') IS NULL
    ALTER TABLE dbo.HUB_Users ADD AccesoAppConfig BIT NOT NULL DEFAULT 0;
GO
