import os
import logging
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from routers import auth, catalogos, kilometros, reportes, tickets, vales, sync, push, passkeys, dispositivo, online, files, legends, conocimiento, dashboard, weather, remisiones

app = FastAPI(title="Field API", version="1.8.0")

# Middleware de debug: loguear requests a ai-extraer-folio ANTES de validación FastAPI.
# Patrón de diagnóstico 422 en uploads: docs/TROUBLESHOOTING.md §5
# (multipart + body_size=0 → problema cliente/SW, no backend).
@app.middleware("http")
async def debug_ai_request(request: Request, call_next):
    if "ai-extraer-folio" in request.url.path:
        ct = request.headers.get("content-type", "none")
        auth_h = request.headers.get("authorization", "none")
        logging.warning(f"[DEBUG AI] method={request.method} content-type={ct[:80]} auth={auth_h[:30]}")
        body = await request.body()
        logging.warning(f"[DEBUG AI] body size={len(body)} first200={body[:200]!r}")
        # Re-crear request con el body ya leído
        async def receive():
            return {"type": "http.request", "body": body, "more_body": False}
        request = Request(request.scope, receive)
    response = await call_next(request)
    return response

# CORS
cors_origins = os.environ.get("CORS_ORIGINS", "http://localhost:5173").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers
app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(catalogos.router, prefix="/api/catalogos", tags=["catalogos"])
app.include_router(kilometros.router, prefix="/api/kilometros", tags=["kilometros"])
app.include_router(reportes.router, prefix="/api/reportes", tags=["reportes"])
app.include_router(tickets.router, prefix="/api/tickets", tags=["tickets"])
app.include_router(vales.router, prefix="/api/vales", tags=["vales"])
app.include_router(sync.router, prefix="/api/sync", tags=["sync"])
app.include_router(push.router, prefix="/api/push", tags=["push"])
app.include_router(passkeys.router, prefix="/api/passkeys", tags=["passkeys"])
app.include_router(dispositivo.router, prefix="/api/dispositivo", tags=["dispositivo"])
app.include_router(online.router, prefix="/api/online", tags=["online"])
app.include_router(files.router, prefix="/api/files", tags=["files"])
app.include_router(legends.router, prefix="/api/legends", tags=["legends"])
app.include_router(conocimiento.router, prefix="/api/conocimiento", tags=["conocimiento"])
app.include_router(dashboard.router, prefix="/api/dashboard", tags=["dashboard"])
app.include_router(weather.router, prefix="/api/weather", tags=["weather"])
app.include_router(remisiones.router, prefix="/api/remisiones", tags=["remisiones"])

# legends_audit corre como proceso propio en supervisor (1 sola instancia).
# NO iniciarlo aquí: uvicorn --workers 2 ejecutaría 2 audits en paralelo
# y duplicaría ScoreLog (carrera en NOT EXISTS).

@app.get("/api/health")
def health():
    return {"status": "ok", "app": "field"}

@app.get("/api/version")
def version():
    return {"version": "1.8.0", "build": "2026-09-16", "name": "Field ECCSA"}
