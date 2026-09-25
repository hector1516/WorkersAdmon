import time
import os

try:
    import bing_wallpaper
except Exception as e:
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Core Import Error: {e}")
    raise SystemExit(1)

# Intervalo en segundos. Default 6 h; se puede sobreescribir con CRON_BING_INTERVAL (segundos)
INTERVAL = int(os.environ.get("CRON_BING_INTERVAL", 21600))
MAX_STORE = int(os.environ.get("CRON_BING_MAX", 5))

print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Starting Bing wallpaper sync service (interval={INTERVAL}s, max={MAX_STORE})...")

while True:
    try:
        new = bing_wallpaper.sync_bing_wallpapers(max_store=MAX_STORE)
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Bing sync done. New: {new}. Max stored: {MAX_STORE}")
    except Exception as err:
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Error in Bing sync loop: {err}")

    time.sleep(INTERVAL)