# Streamlit es OPCIONAL (ver ECCSA-Shell / AGENTS.md): aquí solo se usaba para
# el decorador de caché. Sin Streamlit se usan funciones sin cachear.
try:
    import streamlit as st
except ImportError:  # pragma: no cover - ruta de los workers
    st = None

def _cache(ttl=60):
    """st.cache_data si hay Streamlit; si no, la función se usa tal cual."""
    if st is not None:
        return st.cache_data(ttl=ttl)
    def deco(func):
        return func
    return deco

import base64
import os
import random
import contextlib
import eccsa_db as db

def get_base64_image(image_path):
    if os.path.exists(image_path):
        try:
            with open(image_path, "rb") as img_file:
                return base64.b64encode(img_file.read()).decode()
        except Exception:
            pass
    return ""

@contextlib.contextmanager
def custom_spinner(text="Cargando..."):
    engrane_base64 = get_base64_image("/engrane.png")
    geeky_messages = [
        "Desfragmentando la matriz del hiperespacio...",
        "Compilando flujos de positrones cuánticos...",
        "Calculando el sentido de la vida, el universo y todo lo demás (42)...",
        "Alineando los engranes del servidor de bases de datos...",
        "Cargando condensador de flujo a 1.21 gigavatios...",
        "Calentando las válvulas termiónicas de vacío...",
        "Alimentando a los hámsters que mueven los servidores...",
        "Descargando ram virtual adicional de la nube...",
        "Estabilizando los colectores de antimateria...",
        "Consultando el oráculo digital con bits cuánticos...",
        "Limpiando el polvo de los transistores virtuales...",
        "Acelerando los electrones en el tubo de rayos catódicos..."
    ]
    msg = random.choice(geeky_messages) if text == "Cargando..." else text
    
    spinner_html = f"""
    <div style="display: flex; align-items: center; gap: 15px; margin: 15px 0; padding: 12px 18px; background-color: #1E293B; border-radius: 10px; border: 1px solid #334155; box-shadow: 0 4px 12px rgba(0,0,0,0.3);">
        <div style="
            display: inline-block;
            width: 32px;
            height: 32px;
            background-image: url('data:image/png;base64,{engrane_base64}');
            background-size: contain;
            background-repeat: no-repeat;
            animation: spin-gear 2s linear infinite;
            flex-shrink: 0;
        "></div>
        <div style="color: #FFFFFF; font-weight: 600; font-size: 0.95rem; font-family: 'Outfit', sans-serif;">{msg}</div>
    </div>
    <style>
    @keyframes spin-gear {{
        0% {{ transform: rotate(0deg); }}
        100% {{ transform: rotate(360deg); }}
    }}
    </style>
    """
    placeholder = st.empty()
    placeholder.markdown(spinner_html, unsafe_allow_html=True)
    try:
        yield
    finally:
        placeholder.empty()

@_cache(60)
def load_resumen_cotizaciones():
    return db.get_resumen_cotizaciones()

@_cache(60)
def load_virtual_machines_software():
    return db.get_virtual_machines_software()

def render_header_with_copy_link(title, subtitle, page_param):
    # Resolve Base URL dynamically
    host = st.context.headers.get("Host") or "localhost:8501"
    is_secure = st.context.headers.get("X-Forwarded-Proto") == "https"
    protocol = "https" if is_secure else "http"
    base_url = f"{protocol}://{host}"
    
    full_url = f"{base_url}/?page={page_param}"
    
    st.markdown(
        f"""
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 5px;">
            <div style="display: flex; align-items: center; gap: 12px; position: relative;">
                <h1 style="color: #FFFFFF; margin:0; line-height: 1.1; font-family: 'Outfit', sans-serif;">{title}</h1>
                <button onclick="
                    const el = document.createElement('textarea');
                    el.value = '{full_url}';
                    document.body.appendChild(el);
                    el.select();
                    document.execCommand('copy');
                    document.body.removeChild(el);
                    const t = document.getElementById('copied-toast-header');
                    t.style.display = 'inline-block';
                    setTimeout(() => t.style.display = 'none', 1500);
                " style="background: none; border: none; color: #38BDF8; cursor: pointer; font-size: 1.35rem; padding: 4px; display: flex; align-items: center; transition: transform 0.2s;" title="Copiar enlace directo al portapapeles" onmouseover="this.style.transform='scale(1.15)'" onmouseout="this.style.transform='scale(1.0)'">
                    🔗
                </button>
                <span id="copied-toast-header" style="display: none; color: #4ADE80; font-size: 0.85rem; font-weight: 700; margin-left: 5px; font-family: 'Outfit', sans-serif;">¡Copiado!</span>
            </div>
        </div>
        <p style="color: #94A3B8; margin:0; font-family: 'Outfit', sans-serif;">{subtitle}</p>
        """,
        unsafe_allow_html=True
    )
