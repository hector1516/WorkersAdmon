-- 0068 - Interruptores maestros de canales de salida
--
-- Telegram, WhatsApp (OpenWA) y push PWA se pueden apagar desde el panel.
-- El valor vive en HUB_Config (clave ausente = activo) y lo escribe
-- `canales.guardar` la primera vez que alguien apaga o prende un canal; aquí
-- solo se registra la metadata para que las claves aparezcan en "⚙️ Apps".
--
-- No se siembran valores: el default es 1 (activo) sin fila en HUB_Config.
MERGE dbo.HUB_ConfigCatalogo AS t
USING (VALUES
    ('admon', 'canal_telegram_activo', 'Canal Telegram',
     'Interruptor maestro de los avisos por Telegram. Apagado no sale nada, aunque el worker corra. Se va a retirar: WhatsApp/OpenWA lo reemplaza.',
     'bool', NULL, 40),
    ('admon', 'canal_whatsapp_activo', 'Canal WhatsApp (OpenWA)',
     'Interruptor maestro de avisos, PDFs firmados y respuestas del bot por OpenWA.',
     'bool', NULL, 41),
    ('admon', 'canal_push_activo', 'Canal push PWA',
     'Interruptor maestro de las notificaciones push a los navegadores suscritos.',
     'bool', NULL, 42)
) AS s(App, Clave, Titulo, Descripcion, Tipo, Unidad, Orden)
    ON t.App = s.App AND t.Clave = s.Clave
WHEN NOT MATCHED THEN
    INSERT (App, Clave, Titulo, Descripcion, Tipo, Unidad, Orden)
    VALUES (s.App, s.Clave, s.Titulo, s.Descripcion, s.Tipo, s.Unidad, s.Orden);
GO
