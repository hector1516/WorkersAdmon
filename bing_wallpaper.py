import json
import random
import urllib.request


def _fetch_bing_urls(limit=8):
    url = "https://www.bing.com/HPImageArchive.aspx?format=js&idx=0&n=8"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=4) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    result = []
    for img in data.get("images", []):
        full = "https://www.bing.com" + img.get("url", "")
        if full.startswith("https://www.bing.com"):
            result.append(full)
    return result[:limit]


def _trim_to_max(max_rows):
    from eccsa_db import get_connection
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM HUB_BingWallpapers WHERE Id NOT IN "
                "(SELECT TOP %d Id FROM HUB_BingWallpapers ORDER BY Id DESC)" % max_rows
            )
            conn.commit()


def sync_bing_wallpapers(max_store=5):
    """Trae wallpapers frescos de Bing y deja a lo más `max_store` en BD (rotación)."""
    new_count = 0
    try:
        urls = _fetch_bing_urls(limit=8)
        from eccsa_db import get_connection
        with get_connection() as conn:
            with conn.cursor() as cur:
                for u in urls:
                    cur.execute("SELECT COUNT(*) FROM HUB_BingWallpapers WHERE Url = %s", (u,))
                    if cur.fetchone()[0] == 0:
                        cur.execute("INSERT INTO HUB_BingWallpapers (Url) VALUES (%s)", (u,))
                        new_count += 1
                conn.commit()
        _trim_to_max(max_store)
    except Exception as e:
        print(f"Bing wallpaper sync error: {e}")
    return new_count


def get_active_background():
    """Lee un wallpaper al azar de la BD (rota entre las últimas guardadas). NO llama a la API."""
    default_bgs = [
        "https://images.unsplash.com/photo-1618005182384-a83a8bd57fbe?auto=format&fit=crop&w=1920&q=80",
        "https://images.unsplash.com/photo-1579546929518-9e396f3cc809?auto=format&fit=crop&w=1920&q=80",
        "https://images.unsplash.com/photo-1507525428034-b723cf961d3e?auto=format&fit=crop&w=1920&q=80",
    ]
    try:
        from eccsa_db import get_connection
        with get_connection() as conn:
            with conn.cursor(as_dict=True) as cur:
                cur.execute("SELECT Url FROM HUB_BingWallpapers ORDER BY Id DESC")
                rows = cur.fetchall()
                if rows:
                    return random.choice([r["Url"] for r in rows])
    except Exception as e:
        print(f"Bing wallpaper retrieve error: {e}")
    return random.choice(default_bgs)