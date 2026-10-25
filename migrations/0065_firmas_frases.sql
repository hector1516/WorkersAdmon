-- ============================================================================
-- Migración 0065: Frases aleatorias para firmas de correo
-- ============================================================================
-- Tabla de frases que se pueden insertar aleatoriamente en las firmas.
-- Se elige una frase al cargar la app (Admon/Field) y se inyecta en
-- el placeholder {{frase}} de la plantilla de firma.
-- ============================================================================

IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[HUB_MailboxFirmasFrases]') AND type in (N'U'))
BEGIN
    CREATE TABLE [dbo].[HUB_MailboxFirmasFrases] (
        [Id] INT IDENTITY(1,1) PRIMARY KEY,
        [Frase] NVARCHAR(500) NOT NULL,
        [Autor] NVARCHAR(200) NULL,
        [Activa] BIT NOT NULL DEFAULT 1,
        [Orden] INT NOT NULL DEFAULT 0,
        [Creado] DATETIME NOT NULL DEFAULT GETDATE()
    );
    CREATE INDEX [IX_HUB_MailboxFirmasFrases_Activa] ON [dbo].[HUB_MailboxFirmasFrases]([Activa], [Orden]);
END

-- ============================================================================
-- Sembrar frases iniciales de ECCSA
-- ============================================================================

IF NOT EXISTS (SELECT 1 FROM [dbo].[HUB_MailboxFirmasFrases] WHERE [Frase] = 'La inteligencia es la capacidad de evitar hacer trabajo, y de lograr que el trabajo se haga.')
BEGIN
    INSERT INTO [dbo].[HUB_MailboxFirmasFrases] ([Frase], [Autor], [Orden])
    VALUES ('La inteligencia es la capacidad de evitar hacer trabajo, y de lograr que el trabajo se haga.', 'ECCSA', 1);
END

IF NOT EXISTS (SELECT 1 FROM [dbo].[HUB_MailboxFirmasFrases] WHERE [Frase] = 'El éxito es la suma de pequeños esfuerzos repetidos día tras día.')
BEGIN
    INSERT INTO [dbo].[HUB_MailboxFirmasFrases] ([Frase], [Autor], [Orden])
    VALUES ('El éxito es la suma de pequeños esfuerzos repetidos día tras día.', 'ECCSA', 2);
END

IF NOT EXISTS (SELECT 1 FROM [dbo].[HUB_MailboxFirmasFrases] WHERE [Frase] = 'La mejor manera de predecir el futuro es crearlo.')
BEGIN
    INSERT INTO [dbo].[HUB_MailboxFirmasFrases] ([Frase], [Autor], [Orden])
    VALUES ('La mejor manera de predecir el futuro es crearlo.', 'ECCSA', 3);
END

IF NOT EXISTS (SELECT 1 FROM [dbo].[HUB_MailboxFirmasFrases] WHERE [Frase] = 'Trabajar en equipo divide las tareas y multiplica los resultados.')
BEGIN
    INSERT INTO [dbo].[HUB_MailboxFirmasFrases] ([Frase], [Autor], [Orden])
    VALUES ('Trabajar en equipo divide las tareas y multiplica los resultados.', 'ECCSA', 4);
END

IF NOT EXISTS (SELECT 1 FROM [dbo].[HUB_MailboxFirmasFrases] WHERE [Frase] = 'La calidad no es un acto, es un hábito.')
BEGIN
    INSERT INTO [dbo].[HUB_MailboxFirmasFrases] ([Frase], [Autor], [Orden])
    VALUES ('La calidad no es un acto, es un hábito.', 'ECCSA', 5);
END

IF NOT EXISTS (SELECT 1 FROM [dbo].[HUB_MailboxFirmasFrases] WHERE [Frase] = 'Cada cliente merece nuestra mejor atención, siempre.')
BEGIN
    INSERT INTO [dbo].[HUB_MailboxFirmasFrases] ([Frase], [Autor], [Orden])
    VALUES ('Cada cliente merece nuestra mejor atención, siempre.', 'ECCSA', 6);
END

IF NOT EXISTS (SELECT 1 FROM [dbo].[HUB_MailboxFirmasFrases] WHERE [Frase] = 'Innovar es necesario para seguir siendo relevantes.')
BEGIN
    INSERT INTO [dbo].[HUB_MailboxFirmasFrases] ([Frase], [Autor], [Orden])
    VALUES ('Innovar es necesario para seguir siendo relevantes.', 'ECCSA', 7);
END

IF NOT EXISTS (SELECT 1 FROM [dbo].[HUB_MailboxFirmasFrases] WHERE [Frase] = 'El compromiso con el servicio nos distingue.')
BEGIN
    INSERT INTO [dbo].[HUB_MailboxFirmasFrases] ([Frase], [Autor], [Orden])
    VALUES ('El compromiso con el servicio nos distingue.', 'ECCSA', 8);
END
