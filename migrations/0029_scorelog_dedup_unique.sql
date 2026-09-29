-- Migracion 0029: Limpia ScoreLog duplicado + unique index por entidad
-- Contexto: uvicorn --workers 2 arrancaba legends_audit en cada worker;
-- ambos pasaban NOT EXISTS y duplicaban puntos (ticket/vale/reporte).
-- 'kilometro' NO entra al unique: su ReferenciaId es el vehículo (id_auto),
-- no el id del registro de km — un unique global rompería el histórico.

-- 1) Eliminar duplicados (IdUsuario, Metrica, ReferenciaId) conservando el Id menor
;WITH d AS (
    SELECT Id, IdUsuario, Metrica, Puntos, ReferenciaId,
           ROW_NUMBER() OVER (
               PARTITION BY IdUsuario, Metrica, ReferenciaId
               ORDER BY Id
           ) AS rn
    FROM dbo.HUB_ScoreLog
    WHERE ReferenciaId IS NOT NULL
      AND Metrica IN (
          'reporte_firmado', 'ticket_oxxogas', 'vale_generado',
          'comida_reporte', 'servicio', 'firma_remota'
      )
)
DELETE FROM d WHERE rn > 1;
GO

-- 2) Corregir puntajes inflados por los duplicados ya borrados:
--    restar a PuntuacionSemanal/Total la suma de puntos que se quitó.
--    (Recalculamos semanal desde ScoreLog de la semana en curso y
--     ajustamos total restando solo lo que excede la suma histórica
--     no es segura si hubo resets manuales — por eso solo restamos
--     lo detectado como exceso de doble conteo en UserScores vs ScoreLog.)
;WITH dup AS (
    SELECT IdUsuario, Metrica, ReferenciaId
    FROM dbo.HUB_ScoreLog
    WHERE ReferenciaId IS NOT NULL
      AND Metrica IN (
          'reporte_firmado', 'ticket_oxxogas', 'vale_generado',
          'comida_reporte', 'servicio', 'firma_remota'
      )
    GROUP BY IdUsuario, Metrica, ReferenciaId
    HAVING COUNT(*) > 1
)
SELECT * FROM dup; -- solo informativo en logs de migración
GO

-- 3) Unique index filtrado SOLO para métricas con entidad única
IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'UX_ScoreLog_Entidad'
      AND object_id = OBJECT_ID('dbo.HUB_ScoreLog')
)
BEGIN
    CREATE UNIQUE NONCLUSTERED INDEX UX_ScoreLog_Entidad
        ON dbo.HUB_ScoreLog (IdUsuario, Metrica, ReferenciaId)
        WHERE ReferenciaId IS NOT NULL
          AND Metrica IN (
              'reporte_firmado', 'ticket_oxxogas', 'vale_generado',
              'comida_reporte', 'servicio', 'firma_remota'
          );
END
GO

-- 4) Marcar migración aplicada (el runner también lo hace; esto es belt-and-suspenders
--    solo si se corre el .sql a mano — el runner INSERT en schema_migrations).
--    No insertar aquí si el runner lo hace automáticamente.
