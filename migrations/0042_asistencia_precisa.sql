-- =============================================================================
-- 0042 · ASISTENCIA MÁS EXACTA — evidencia del escaneo y ventana de
--          incertidumbre
--
-- POR QUÉ
-- -------
-- La asistencia se infiere de la red: `network_scanner.py` ve el MAC del
-- teléfono/laptop en la red de la oficina y registra un ENTRADA o un SALIDA en
-- HUB_NetworkPresence. Eso son dos problemas medidos, ni adivinados:
--
--   1. El evento se sellaba con GETDATE() —la hora en que el WORKER procesó el
--      escaneo— y no con la hora en que el escaneo VIÓ el dispositivo. Con el
--      worker cada 60s y el escáner cada 180s (`net_scan_interval_seg`), eso
--      mete entre 1 y 4 minutos de retraso artificial encima del muestreo.
--
--   2. `calcular_asistencia_dia` guardaba UN minuto (HoraEntradaReal) como si
--      fuera exacto. Con escaneo cada 3 minutos, la verdad es un intervalo:
--      la persona llegó en algún momento entre "la última vez que se la vio
--      fuera" y "la primera vez que se la vio dentro". Un solo minuto es
--      ficción, y además se compara contra la tolerancia para marcar
--      "tarde", así que la precisión inventada se convierte en un injusto
--      real o en un falso positivo.
--
-- Y dos que son peor que la imprecisión porque son FALSOS POSITIVOS:
--
--   3. `ausente = (entrada_real is None)`: si el escáner se caía, no había
--      FechaScan, no había eventos, y TODO el mundo quedaba AUSENTE. Una caída
--      de red se convertía en "¿ausentismo masivo?". Por eso se separa
--      SIN_DATOS (no hay evidencia porque no se escaneó) de AUSENTE (hay
--      evidencia y no hay presencia).
--
--   4. El fallback tomaba `TOP 1 FechaScan ORDER BY FechaScan` (el primero del
--      día). Un laptop encendido desde la noche aparecía a las 00:15 y el
--      cálculo decía "llegó a las 00:15" → nunca llegó tarde, y sin que nadie
--      lo notara. Ahora solo se considera presencia dentro de una ventana
--      alrededor del turno (`net_asistencia_ventana_min`).
--
-- QUÉ AGREGA
-- ---------
-- HUB_NetworkPresence:
--   FechaDeteccion     instante del ESCANEO que prueba el evento. FechaHora
--                      queda siendo el instante de procesamiento (auditoría):
--                      si se comparan, la diferencia ES el retraso del worker.
--   UltimaVezVisto     el otro extremo de la ventana.
--   VentanaMin   ancho de la ventana, en minutos.
--   Origen             RED | MANUAL (para no confundir lo automático con lo
--                      que.capture una persona).
--
-- HUB_AsistenciaDiaria:
--   HoraEntradaEsperada / HoraSalidaEsperada   el turno de ESE día, congelado.
--      Antes la vista pintaba "Entrada Esperada" con la hora REAL duplicada
--      porque la tabla no guardaba la esperada: no se podía ni ver el desfase.
--   EntradaPiso/Techo, SalidaPiso/Techo        la ventana real.
--   VentanaMin                        qué tan ancho es el dato.
--   Estado  CALCULADA | SIN_DATOS | AUSENTE | NO_APLICA | INDETERMINADO
--   Indeterminado  1 = la ventana CRUZA la tolerancia, no se puede afirmar.
--   EscaneosDia    cuántos escaneos_OK hubo ese día: la evidencia de que el
--                  sistema estaba vivo (es lo que separa SIN_DATOS de AUSENTE).
--   Fuente         RED | MANUAL
-- =============================================================================

-- ── 1) Evidencia en el evento de presencia ─────────────────────────────────
IF COL_LENGTH('dbo.HUB_NetworkPresence', 'FechaDeteccion') IS NULL
    ALTER TABLE dbo.HUB_NetworkPresence ADD FechaDeteccion DATETIME NULL;
GO
IF COL_LENGTH('dbo.HUB_NetworkPresence', 'UltimaVezVisto') IS NULL
    ALTER TABLE dbo.HUB_NetworkPresence ADD UltimaVezVisto DATETIME NULL;
GO
IF COL_LENGTH('dbo.HUB_NetworkPresence', 'VentanaMin') IS NULL
    ALTER TABLE dbo.HUB_NetworkPresence ADD VentanaMin INT NULL;
GO
IF COL_LENGTH('dbo.HUB_NetworkPresence', 'Origen') IS NULL
    ALTER TABLE dbo.HUB_NetworkPresence
        ADD Origen VARCHAR(10) NOT NULL DEFAULT 'RED';
GO
-- Los eventos previos no tienen evidencia: se deja NULL a propósito y el
-- cálculo cae a FechaHora. Marcarlo RED sería mentir sobre un dato que no
-- sabe cuándo se detectó.
UPDATE dbo.HUB_NetworkPresence
SET FechaDeteccion = FechaHora
WHERE FechaDeteccion IS NULL;
GO

-- ── 2) Ventana de incertidumbre y estado real en la asistencia ────────────
IF COL_LENGTH('dbo.HUB_AsistenciaDiaria', 'HoraEntradaEsperada') IS NULL
    ALTER TABLE dbo.HUB_AsistenciaDiaria ADD HoraEntradaEsperada TIME NULL;
GO
IF COL_LENGTH('dbo.HUB_AsistenciaDiaria', 'HoraSalidaEsperada') IS NULL
    ALTER TABLE dbo.HUB_AsistenciaDiaria ADD HoraSalidaEsperada TIME NULL;
GO
IF COL_LENGTH('dbo.HUB_AsistenciaDiaria', 'EntradaPiso') IS NULL
    ALTER TABLE dbo.HUB_AsistenciaDiaria ADD EntradaPiso TIME NULL;
GO
IF COL_LENGTH('dbo.HUB_AsistenciaDiaria', 'EntradaTecho') IS NULL
    ALTER TABLE dbo.HUB_AsistenciaDiaria ADD EntradaTecho TIME NULL;
GO
IF COL_LENGTH('dbo.HUB_AsistenciaDiaria', 'SalidaPiso') IS NULL
    ALTER TABLE dbo.HUB_AsistenciaDiaria ADD SalidaPiso TIME NULL;
GO
IF COL_LENGTH('dbo.HUB_AsistenciaDiaria', 'SalidaTecho') IS NULL
    ALTER TABLE dbo.HUB_AsistenciaDiaria ADD SalidaTecho TIME NULL;
GO
IF COL_LENGTH('dbo.HUB_AsistenciaDiaria', 'VentanaMin') IS NULL
    ALTER TABLE dbo.HUB_AsistenciaDiaria ADD VentanaMin INT NULL;
GO
IF COL_LENGTH('dbo.HUB_AsistenciaDiaria', 'Indeterminado') IS NULL
    ALTER TABLE dbo.HUB_AsistenciaDiaria
        ADD Indeterminado BIT NOT NULL DEFAULT 0;
GO
IF COL_LENGTH('dbo.HUB_AsistenciaDiaria', 'EscaneosDia') IS NULL
    ALTER TABLE dbo.HUB_AsistenciaDiaria ADD EscaneosDia INT NULL;
GO
-- El mayor hueco entre escaneos consecutivos dentro de la ventana del turno.
-- OJO: NO se puede usar "cuántos escaneos hubo" como señal de vida del
-- escáner, porque save_results() no escribe NADA cuando un escaneo no detecta
-- ningún equipo (network_scanner_windows.py: `if not devices: return 0`): de
-- madrugada, con la oficina vacía, el escáner puede estar corriendo perfectamente
-- y no dejar rastro. El hueco máximo sí lo delata: si entre dos escaneos
-- pasaron 40 minutos, el escáner se cayó.
IF COL_LENGTH('dbo.HUB_AsistenciaDiaria', 'GapMaximoMin') IS NULL
    ALTER TABLE dbo.HUB_AsistenciaDiaria ADD GapMaximoMin INT NULL;
GO
IF COL_LENGTH('dbo.HUB_AsistenciaDiaria', 'Fuente') IS NULL
    ALTER TABLE dbo.HUB_AsistenciaDiaria
        ADD Fuente VARCHAR(10) NOT NULL DEFAULT 'RED';
GO
IF COL_LENGTH('dbo.HUB_AsistenciaDiaria', 'Estado') IS NULL
    ALTER TABLE dbo.HUB_AsistenciaDiaria
        ADD Estado VARCHAR(20) NOT NULL DEFAULT 'CALCULADA';
GO

-- Los registros viejos no tienen ventana calculada: se deja Estado en
-- CALCULADA y el próximo recálculo los rellena. NO se inventa una ventana
-- a partir de un minuto que ya se sabe que no era exacto.
UPDATE dbo.HUB_AsistenciaDiaria
SET Estado = CASE WHEN Ausente = 1 THEN 'AUSENTE' ELSE 'CALCULADA' END
WHERE Estado = 'CALCULADA' AND (VentanaMin IS NULL);
GO

-- ── 3) Configuración ───────────────────────────────────────────────────────
-- La tolerancia de ENTRADA no es una tolerancia de puntualidad: es un retardo
-- artificial que sumaba 2 min a todos. El debounce que de verdad evita falsos
-- positivos es el de SALIDA (que un AP no marque por perder un escaneo). Con
-- entrada en 0, cualquier reaparición tras un FUERA es una entrada.
-- OJO: sin punto y coma antes del ELSE. En T-SQL el ';' cierra el IF y deja
-- el ELSE solo, que es "Incorrect syntax near the keyword 'WHERE'".
IF NOT EXISTS (SELECT 1 FROM dbo.HUB_Config WHERE Clave = 'net_tolerance_entrada_min')
    INSERT INTO dbo.HUB_Config (Clave, Valor) VALUES ('net_tolerance_entrada_min', '0')
ELSE
    UPDATE dbo.HUB_Config SET Valor = '0' WHERE Clave = 'net_tolerance_entrada_min';
GO

-- Ventana alrededor del turno en la que se considera que hay evidencia de
-- presencia. Sirve para que un equipo encendido de madrugada no se cuente
-- como "llegó a las 00:15" (y salga luego como puntual toda la semana).
IF NOT EXISTS (SELECT 1 FROM dbo.HUB_Config WHERE Clave = 'net_asistencia_ventana_min')
    INSERT INTO dbo.HUB_Config (Clave, Valor) VALUES ('net_asistencia_ventana_min', '120');
GO

-- Mínimo de escaneos exitosos en el día para poder afirmar algo. Por debajo de
-- esto el día es SIN DATOS, no AUSENTE. Con el intervalo de 180s son ~288 al
-- día, así que 20 es holgadamente bajo y solo salta cuando el escáner estuvo
-- caído.
IF NOT EXISTS (SELECT 1 FROM dbo.HUB_Config WHERE Clave = 'net_asistencia_escaneos_minimos')
    INSERT INTO dbo.HUB_Config (Clave, Valor) VALUES ('net_asistencia_escaneos_minimos', '20');
GO

-- Política de redondeo cuando la ventana es más ancha que la tolerancia:
-- PISO (el más benévolo para el trabajador) | TECHO (el más estricto) |
-- PROMEDIO. Se guarda aunque hoy solo se use TECHO, para que el cambio de
-- política sea un UPDATE y no una migración.
IF NOT EXISTS (SELECT 1 FROM dbo.HUB_Config WHERE Clave = 'net_asistencia_politica')
    INSERT INTO dbo.HUB_Config (Clave, Valor) VALUES ('net_asistencia_politica', 'PISO');
GO
