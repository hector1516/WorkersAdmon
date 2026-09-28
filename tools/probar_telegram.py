#!/usr/bin/env python3
"""Prueba la plantilla de Telegram sin registrar un ticket de verdad.

Sirve para el caso de los OxxoGas, donde la alerta salia a Telegram con los
placeholders literales ({Nombre}, {Cantidad}, {Cliente}, {Descripcion}) en
lugar de los valores.

Por defecto NO envia nada: solo lee la plantilla real de
HUB_TelegramEventos, la renderiza con un payload de prueba y la imprime. Asi
se ve de inmediato si la sustitución quedo bien, sin ensuciar el canal de
Telegram ni la cola.

Para mandarla de verdad (a los destinatarios configurados del evento):

    python3 tools/probar_telegram.py --evento OXXOGAS_TICKET --enviar

Se ejecuta dentro del contenedor, desde /app/api para que 'telegram_hub' y
'db' resuelvan igual que en el proceso real:

    docker exec -w /app/api workersadmon python3 /app/tools/probar_telegram.py
"""
import argparse
import os
import sys

# El proceso real corre con /app/api en el path (por eso api/routers/sync.py
# puede hacer "import telegram_hub" a pelo). Si se corre desde otro lado se
# agrega para que el script funcione igual.
RAIZ_API = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "api")
if os.path.isdir(RAIZ_API) and RAIZ_API not in sys.path:
    sys.path.insert(0, RAIZ_API)

# Payload de prueba. Los nombres planos son los que pide la plantilla; los
# internos se incluyen a proposito para comprobar que los alias tambien
# funcionan (fue justo lo que rompio en produccion: cada copia del codigo
# nombra los campos distinto y la plantilla usa los nombres planos).
PAYLOAD_PRUEBA = {
    "Fecha": "27/09/2026",
    "Nombre": "USUARIO DE PRUEBA",
    "Cantidad": "40 L",
    "Folio": "000000000",
    "Auto": "SNE979 (Kia Rio 21)",
    "Cliente": "CLIENTE DE PRUEBA",
    "Descripcion": "Ticket de prueba, se puede borrar",
    # Nombres internos, para verificar los alias.
    "NombreRegistro": "USUARIO DE PRUEBA",
    "FolioTicket": "000000000",
    "Empresa": "CLIENTE DE PRUEBA",
    "ProyectoServicio": "Ticket de prueba, se puede borrar",
}


def main():
    ap = argparse.ArgumentParser(description="Prueba la plantilla de Telegram")
    ap.add_argument("--evento", default="OXXOGAS_TICKET")
    ap.add_argument("--enviar", action="store_true",
                    help="Encola y envia de verdad (sale a Telegram)")
    ap.add_argument("--sin-alias", action="store_true",
                    help="Probar solo con los nombres internos de la plantilla")
    args = ap.parse_args()

    import telegram_hub

    plantilla, adj_config, activo = telegram_hub._get_event_template(args.evento)
    if not plantilla:
        print(f"El evento {args.evento} no tiene plantilla en HUB_TelegramEventos.")
        return 1
    if not activo:
        print(f"Aviso: el evento {args.evento} esta desactivado; no se encolaria nada.")

    datos = dict(PAYLOAD_PRUEBA)
    if args.sin_alias:
        for k in ("Nombre", "Cantidad", "Folio", "Auto", "Cliente", "Descripcion"):
            datos.pop(k, None)

    print("=" * 60)
    print(f"PLANTILLA DE {args.evento}")
    print("=" * 60)
    print(plantilla.strip())
    print("-" * 60)

    texto = telegram_hub._apply_template(plantilla, datos)

    import re
    sin_resolver = sorted(set(re.findall(r"\{(\w+)\}", texto)))
    print("RESULTADO:")
    print(texto)
    print("-" * 60)
    if sin_resolver:
        print(f"FALLA: quedaron placeholders sin sustituir: {sin_resolver}")
        return 1
    print("OK: no queda ningun placeholder sin sustituir.")

    if not args.enviar:
        print("\n(Simulacion. No se envio nada. Usa --enviar para mandarlo.)")
        return 0

    print("\nEncolando de verdad...")
    n = telegram_hub._queue(args.evento, datos, None, None)
    if n:
        print(f"Encolado en {n} destinatario(s). Debe llegar en el proximo ciclo del worker.")
    else:
        print("No se encolo nada. Revisa que el evento tenga destinatarios "
              "configurados (HUB_TelegramDestinatarios).")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
