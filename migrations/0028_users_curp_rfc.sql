-- Migracion 0028: Campo CURP/RFC en HUB_Users
IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('HUB_Users') AND name = 'CurpRfc')
BEGIN
    ALTER TABLE dbo.HUB_Users ADD CurpRfc VARCHAR(20) NULL;
END
GO
