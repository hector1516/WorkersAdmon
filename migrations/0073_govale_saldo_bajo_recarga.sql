-- 0073 · Aviso de saldo bajo con datos de recarga y tope de recordatorios
--
-- Qué cambia:
--   1. `govale_umbral` (default 2000): umbral del aviso de saldo bajo.
--   2. `govale_recordatorio_dias_max` (default 15): un vale más viejo ya no
--      recibe recordatorios de ticket (no se va a subir; el aviso solo molesta).
--   3. El texto de GOVALE_SALDO_BAJO ahora dice cómo recargar (banco, cuenta,
--      CLABE, referencia). Se actualiza en WhatsApp y se siembra el evento
--      equivalente de Telegram, que estaba solo para el aviso diario.
--
-- Idempotente: se puede ejecutar más de una vez. Las claves de HUB_Config solo
-- se insertan si faltan, para no pisar un valor ya configurado desde el panel.
-- ============================================================================

IF NOT EXISTS (SELECT 1 FROM dbo.HUB_Config WHERE Clave = 'govale_umbral')
    INSERT INTO dbo.HUB_Config (Clave, Valor) VALUES ('govale_umbral', '2000');
GO

IF NOT EXISTS (SELECT 1 FROM dbo.HUB_Config WHERE Clave = 'govale_recordatorio_dias_max')
    INSERT INTO dbo.HUB_Config (Clave, Valor) VALUES ('govale_recordatorio_dias_max', '15');
GO

-- WhatsApp: mismo evento, texto con los datos de la transferencia.
IF OBJECT_ID('dbo.HUB_WhatsappEventos', 'U') IS NOT NULL
    UPDATE dbo.HUB_WhatsappEventos
    SET PlantillaMensaje = REPLACE(
            N'Saldo bajo de vales de gasolina: quedan ${Saldo}.~~Para recargar, haz tu transferencia:~~Banco: BBVA Bancomer~Cuenta: 0120809551~CLABE: *{Clabe}*~Razón social: SERVICIOS GASOLINEROS DE MEXICO~RFC: SGM950714DC2~Dirección: EDISON NORTE 1301 SN TALLERES MONTERREY NUEVO LEÓN~CP: 64480~~En el campo Motivo/Concepto/Descripción escribe tu referencia única de cliente: *{Referencia}*~~Cuando hagas la transferencia, manda el comprobante para aplicar la recarga.',
            '~', CHAR(13) + CHAR(10)),
        Actualizado = GETDATE()
    WHERE IdEvento = N'GOVALE_SALDO_BAJO';
GO

-- Telegram: el mismo aviso como evento propio (hoy el canal está apagado; si
-- se prende, tiene que salir con el mismo texto).
IF OBJECT_ID('dbo.HUB_TelegramEventos', 'U') IS NOT NULL
BEGIN
    IF NOT EXISTS (SELECT 1 FROM dbo.HUB_TelegramEventos WHERE IdEvento = 'GOVALE_SALDO_BAJO')
        INSERT INTO dbo.HUB_TelegramEventos
            (IdEvento, Nombre, PlantillaMensaje, AdjuntarArchivo, Activo)
        SELECT N'GOVALE_SALDO_BAJO', N'Saldo Go Vale bajo',
               REPLACE(
                   N'Saldo bajo de vales de gasolina: quedan ${Saldo}.~~Para recargar, haz tu transferencia:~~Banco: BBVA Bancomer~Cuenta: 0120809551~CLABE: *{Clabe}*~Razón social: SERVICIOS GASOLINEROS DE MEXICO~RFC: SGM950714DC2~Dirección: EDISON NORTE 1301 SN TALLERES MONTERREY NUEVO LEÓN~CP: 64480~~En el campo Motivo/Concepto/Descripción escribe tu referencia única de cliente: *{Referencia}*~~Cuando hagas la transferencia, manda el comprobante para aplicar la recarga.',
                   '~', CHAR(13) + CHAR(10)),
               0, 1;
    ELSE
        UPDATE dbo.HUB_TelegramEventos
        SET PlantillaMensaje = REPLACE(
                N'Saldo bajo de vales de gasolina: quedan ${Saldo}.~~Para recargar, haz tu transferencia:~~Banco: BBVA Bancomer~Cuenta: 0120809551~CLABE: *{Clabe}*~Razón social: SERVICIOS GASOLINEROS DE MEXICO~RFC: SGM950714DC2~Dirección: EDISON NORTE 1301 SN TALLERES MONTERREY NUEVO LEÓN~CP: 64480~~En el campo Motivo/Concepto/Descripción escribe tu referencia única de cliente: *{Referencia}*~~Cuando hagas la transferencia, manda el comprobante para aplicar la recarga.',
                '~', CHAR(13) + CHAR(10))
        WHERE IdEvento = N'GOVALE_SALDO_BAJO';
END
GO
