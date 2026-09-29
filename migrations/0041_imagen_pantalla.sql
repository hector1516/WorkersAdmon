-- Migración 0041: imagen de portada del Dashboard (pantalla "portada")
-- Fecha: 2026-09-28
-- Descripción: Una imagen a pantalla completa (1920x1080) que la TV muestra con
--              un título grande encima, por ejemplo "ECCSA en Octubre". La sube
--              una persona desde el módulo Notas de Admon y la TV la toma sola.
--
-- Por qué en la base y no por HTTP directo entre Admon y el Dashboard: las dos
-- apps ya leen esta misma base, así que la imagen viaja por donde ya viaja todo
-- lo demás, no hace falta un endpoint de subida ni un token compartido, y si
-- la TV está apagada la imagen sigue ahí cuando vuelve.
--
-- Una sola tabla, no un par de filas: se guarda la ACTIVA (Activo=1) y las
-- anteriores se desactivan, para poder volver a una anterior sin volver a
-- subirla.

IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'HUB_PantallaImagenes')
BEGIN
    CREATE TABLE HUB_PantallaImagenes (
        Id INT IDENTITY(1,1) PRIMARY KEY,
        -- 'PORTADA' es la clave de la pantalla del mes; queda abierta para
        -- añadir más pantallas con imagen propia más adelante.
        Clave NVARCHAR(50) NOT NULL,
        -- Texto grande sobrepuesto en el centro de la imagen. Si viene vacío,
        -- el Dashboard arma "ECCSA en <mes actual>" por su cuenta.
        Titulo NVARCHAR(120) NULL,
        -- La imagen cruda. VARBINARY(MAX) y no IMAGE: los bytes de una foto
        -- moderna (1920x1080) pasan de los 65 KB de IMAGE.
        Archivo VARBINARY(MAX) NOT NULL,
        ContentType NVARCHAR(80) NOT NULL DEFAULT 'image/jpeg',
        -- Dimensiones reales, sólo informativas: sirve para avisar en la UI si
        -- la imagen no es de 1920x1080 (se vería estirada).
        Ancho INT NULL,
        Alto INT NULL,
        IdUsuario INT NULL,
        FechaSubida DATETIME NOT NULL DEFAULT GETDATE(),
        Activo BIT NOT NULL DEFAULT 1,
        FOREIGN KEY (IdUsuario) REFERENCES HUB_Users(Id)
    );

    PRINT 'Tabla HUB_PantallaImagenes creada';
END
ELSE
BEGIN
    PRINT 'Tabla HUB_PantallaImagenes ya existe';
END
GO

-- Índice único: una sola imagen activa por clave. La TV no puede dejar de ver
-- la portada porque se subiieron dos a la vez.
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'UX_HUB_PantallaImagenes_Activo')
BEGIN
    CREATE UNIQUE INDEX UX_HUB_PantallaImagenes_Activo
        ON HUB_PantallaImagenes (Clave)
        WHERE Activo = 1;
    PRINT 'Índice único de imagen activa creado';
END
GO
