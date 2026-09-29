-- 0033_push_subscriptions_schema.sql
-- La tabla HUB_PushSubscriptions en producción quedó con un esquema camelCase
-- (id, endpoint, userId, p256dh, auth, createdAt, updatedAt) que NO coincide con
-- el baseline 0002 ni con el código de eccsa_db (UserEmail, Endpoint, P256dhKey, AuthKey).
-- Resultado: get_all_push_subscriptions / save_push_subscription fallan con
-- "Invalid column name 'UserEmail'" y no se puede enviar ni guardar push.
--
-- La tabla está vacía (0 filas) al momento de escribir esta migración, por lo que
-- recrearla es seguro y alinea BD ↔ código.

IF EXISTS (SELECT 1 FROM sys.tables WHERE name = 'HUB_PushSubscriptions')
BEGIN
    DROP TABLE dbo.[HUB_PushSubscriptions];
END
GO

CREATE TABLE dbo.[HUB_PushSubscriptions] (
    [Id] int NOT NULL IDENTITY,
    [UserEmail] nvarchar(510) NULL,
    [Endpoint] nvarchar(2048) NULL,
    [P256dhKey] nvarchar(512) NULL,
    [AuthKey] nvarchar(512) NULL,
    [CreatedAt] datetime NULL CONSTRAINT DF_HUB_PushSubscriptions_CreatedAt DEFAULT GETDATE()
);
GO
