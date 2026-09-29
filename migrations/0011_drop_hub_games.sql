-- 0011_drop_hub_games.sql
-- Migración: eliminar tabla HUB_Games (no utilizada por ninguna vista/módulo).
-- Tabla heredada del baseline 0002; solo contenía marcadores de prueba.

IF OBJECT_ID('dbo.HUB_Games', 'U') IS NOT NULL
    DROP TABLE dbo.HUB_Games;
GO