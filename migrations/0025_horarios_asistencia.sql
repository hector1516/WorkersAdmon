-- 0025_horarios_asistencia.sql
-- Turnos, asignación usuario-turno y registro diario de asistencias

-- 1) Catálogo de turnos
IF OBJECT_ID('dbo.HUB_Turnos', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.HUB_Turnos (
        Id              INT IDENTITY(1,1) PRIMARY KEY,
        Nombre          VARCHAR(50) NOT NULL,          -- 'Operativo', 'Administrativo'
        Descripcion     VARCHAR(200) NULL,
        Activo          BIT NOT NULL DEFAULT 1,
        -- Horarios Lunes a Viernes
        LV_Entrada      TIME NOT NULL,                  -- 09:00 o 08:00
        LV_Salida       TIME NOT NULL,                  -- 18:30
        -- Horarios Sábado
        Sab_Entrada     TIME NULL,                      -- 09:30 o NULL
        Sab_Salida      TIME NULL,                      -- 13:30 o NULL
        -- Domingos (normalmente NULL)
        Dom_Entrada     TIME NULL,
        Dom_Salida      TIME NULL,
        -- Tolerancias en minutos
        Tol_Llegada_Min     INT NOT NULL DEFAULT 15,    -- tolerancia llegada (minutos)
        Tol_Salida_Antes_Min INT NOT NULL DEFAULT 5,    -- min antes de hora salida permitido
        Tol_Salida_Despues_Min INT NOT NULL DEFAULT 0,  -- min después de hora salida (opcional)
        FechaCreacion   DATETIME NOT NULL DEFAULT GETDATE()
    );
END
GO

-- Insertar turnos por defecto
IF NOT EXISTS (SELECT 1 FROM dbo.HUB_Turnos WHERE Nombre = 'Operativo')
BEGIN
    INSERT INTO dbo.HUB_Turnos (Nombre, Descripcion, LV_Entrada, LV_Salida, Sab_Entrada, Sab_Salida, Tol_Llegada_Min, Tol_Salida_Antes_Min)
    VALUES ('Operativo', 'Lunes a Viernes 9:00-18:30, Sábado 9:30-13:30', '09:00', '18:30', '09:30', '13:30', 15, 5);
END
GO

IF NOT EXISTS (SELECT 1 FROM dbo.HUB_Turnos WHERE Nombre = 'Administrativo')
BEGIN
    INSERT INTO dbo.HUB_Turnos (Nombre, Descripcion, LV_Entrada, LV_Salida, Sab_Entrada, Sab_Salida, Tol_Llegada_Min, Tol_Salida_Antes_Min)
    VALUES ('Administrativo', 'Lunes a Viernes 8:00-18:30', '08:00', '18:30', NULL, NULL, 15, 5);
END
GO

-- 2) Asignación usuario → turno
IF OBJECT_ID('dbo.HUB_UsuarioTurno', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.HUB_UsuarioTurno (
        Id              INT IDENTITY(1,1) PRIMARY KEY,
        IdUsuario       INT NOT NULL,
        IdTurno         INT NOT NULL,
        FechaDesde      DATE NOT NULL DEFAULT GETDATE(),
        FechaHasta      DATE NULL,                       -- NULL = vigente
        Activo          BIT NOT NULL DEFAULT 1,
        CONSTRAINT FK_UsuarioTurno_Usuario FOREIGN KEY (IdUsuario) REFERENCES dbo.HUB_Users(Id),
        CONSTRAINT FK_UsuarioTurno_Turno FOREIGN KEY (IdTurno) REFERENCES dbo.HUB_Turnos(Id)
        -- OJO: aquí había un
        --     CONSTRAINT UQ_UsuarioTurno_Vigente UNIQUE (IdUsuario, Activo) WHERE Activo = 1
        -- y es T-SQL inválido: un índice FILTRADO no se puede declarar como
        -- constraint dentro del CREATE TABLE, hay que hacerlo con un
        -- CREATE UNIQUE INDEX ... WHERE aparte. Con la línea ahí, aplicar esta
        -- migración fallaba con "Incorrect syntax near the keyword 'WHERE'" y
        -- quien la encontrara iba a buscar el error en su propia migración.
        --
        -- Se quita en vez de "arreglarse" porque en producción la tabla se creó
        -- SIN ese índice (solo PK, defaults y las dos FK): el archivo tiene que
        -- describir lo que hay, no prometer algo que nunca existió. Si algún
        -- día se quiere la garantía de un solo turno vigente por usuario, es una
        -- migración nueva que se aplique en todos lados:
        --     CREATE UNIQUE INDEX UQ_UsuarioTurno_Vigente
        --         ON dbo.HUB_UsuarioTurno(IdUsuario) WHERE Activo = 1;
    );
    CREATE INDEX IX_UsuarioTurno_Usuario ON dbo.HUB_UsuarioTurno(IdUsuario);
END
GO

-- 3) Registro diario de asistencias (una fila por usuario por día)
IF OBJECT_ID('dbo.HUB_AsistenciaDiaria', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.HUB_AsistenciaDiaria (
        Id                  INT IDENTITY(1,1) PRIMARY KEY,
        IdUsuario           INT NOT NULL,
        Fecha               DATE NOT NULL,
        IdTurno             INT NULL,                     -- Turno asignado ese día
        HoraEntradaReal     TIME NULL,                    -- Primera detección en red
        HoraSalidaReal      TIME NULL,                    -- Última detección en red
        EntradaTardia       BIT NOT NULL DEFAULT 0,       -- 1 = llegó tarde (> tolerancia)
        MinutosTarde        INT NULL,                     -- Minutos de retraso
        SalidaTemprana      BIT NOT NULL DEFAULT 0,       -- 1 = se fue antes de hora
        MinutosAntes        INT NULL,                     -- Minutos antes de hora salida
        Ausente             BIT NOT NULL DEFAULT 0,       -- 1 = no hubo detección
        Observaciones       VARCHAR(500) NULL,
        FechaCalculo        DATETIME NOT NULL DEFAULT GETDATE(),
        CONSTRAINT FK_Asistencia_Usuario FOREIGN KEY (IdUsuario) REFERENCES dbo.HUB_Users(Id),
        CONSTRAINT FK_Asistencia_Turno FOREIGN KEY (IdTurno) REFERENCES dbo.HUB_Turnos(Id),
        CONSTRAINT UQ_Asistencia_Usuario_Fecha UNIQUE (IdUsuario, Fecha)
    );
    CREATE INDEX IX_Asistencia_Fecha ON dbo.HUB_AsistenciaDiaria(Fecha);
    CREATE INDEX IX_Asistencia_Usuario ON dbo.HUB_AsistenciaDiaria(IdUsuario);
END
GO

-- 4) Config de tolerancias globales (opcional, sobrescribe turno si se usa)
IF NOT EXISTS (SELECT 1 FROM dbo.HUB_Config WHERE Clave = 'asistencia_tol_llegada_min')
    INSERT INTO dbo.HUB_Config (Clave, Valor) VALUES ('asistencia_tol_llegada_min', '15');
GO
IF NOT EXISTS (SELECT 1 FROM dbo.HUB_Config WHERE Clave = 'asistencia_tol_salida_antes_min')
    INSERT INTO dbo.HUB_Config (Clave, Valor) VALUES ('asistencia_tol_salida_antes_min', '5');
GO
IF NOT EXISTS (SELECT 1 FROM dbo.HUB_Config WHERE Clave = 'asistencia_calcular_auto')
    INSERT INTO dbo.HUB_Config (Clave, Valor) VALUES ('asistencia_calcular_auto', '1');
GO