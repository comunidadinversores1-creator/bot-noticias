"""Historias de Instagram (1080x1920), con el mismo diseño que el carrusel "¿Qué es Bitcoin?".

Se arman en HTML y se sacan como foto con el navegador (Playwright). En GitHub se usa el Chrome que ya
viene instalado en la máquina, así que no hay que bajar nada pesado.

Zonas seguras: Instagram tapa arriba (~250 px: foto de perfil y barras) y abajo (~340 px: "Enviar mensaje").

Tipos:
  agenda    -> los datos económicos del día (@ezebagnato, a la mañana)
  aviso     -> "en 1 hora sale..." antes de un dato fuerte (@ezebagnato)
  carrusel  -> "nuevo carrusel" con la portada, el día que sale uno (@ezebagnato)
  promo     -> la imagen de una promo + "el link está en mi perfil" (@ezebagnato, cada 2 días)
  noticia   -> la placa de una noticia recién publicada (@comunidad_inversores_ok)"""
from __future__ import annotations

import base64
import html
import io
from pathlib import Path

from .util import RAIZ, log

LETRAS = RAIZ / "assets" / "fuentes_letra"
LOGO = RAIZ / "assets" / "logo.png"
e = lambda t: html.escape(str(t or ""), quote=True)


def _datos(img: bytes | None, tipo: str = "image/jpeg") -> str:
    return f"data:{tipo};base64,{base64.b64encode(img).decode()}" if img else ""


def _logo() -> str:
    try:
        return _datos(LOGO.read_bytes(), "image/png")
    except OSError:
        return ""


CSS = """
@font-face { font-family: "Anton"; src: url("file://LETRAS/Anton-Regular.ttf"); }
@font-face { font-family: "Inter"; font-weight: 500; src: url("file://LETRAS/Inter-Medium.otf"); }
@font-face { font-family: "Inter"; font-weight: 600; src: url("file://LETRAS/Inter-SemiBold.otf"); }
@font-face { font-family: "Inter"; font-weight: 700; src: url("file://LETRAS/Inter-Bold.otf"); }
:root { --verde:#22c55e; --naranja:#f7931a; --rojo:#ef4444; --negro:#09090b; --texto:#e4e4e7; --gris:#a1a1aa; }
* { box-sizing: border-box; margin: 0; padding: 0; }
body { background: #000; font-family: "Inter"; font-weight: 500; }
.h { width: 1080px; height: 1920px; position: relative; overflow: hidden; background: var(--negro); color: var(--texto); }
.h::before { content:""; position:absolute; inset:0; background-image: linear-gradient(rgba(255,255,255,.035) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,.035) 1px, transparent 1px); background-size: 72px 72px; }
.h::after { content:""; position:absolute; inset:0; background: radial-gradient(1000px 800px at 90% -5%, rgba(34,197,94,.18), transparent 60%), radial-gradient(900px 700px at -10% 105%, rgba(247,147,26,.10), transparent 60%); }
.c { position: absolute; left: 84px; right: 84px; top: 270px; bottom: 360px; z-index: 2; display: flex; flex-direction: column; }
.tag { align-self: flex-start; font-weight: 700; font-size: 30px; letter-spacing: .16em; text-transform: uppercase; color: var(--negro); background: var(--verde); padding: 12px 26px; border-radius: 99px; }
.tag.n { background: var(--naranja); } .tag.r { background: var(--rojo); color: #fff; }
h1 { font-family: "Anton"; font-weight: 400; text-transform: uppercase; line-height: 1.08; color: #fff; font-size: 104px; margin-top: 30px; }
h1 em { font-style: normal; color: var(--verde); }
.sub { font-size: 40px; color: var(--gris); font-weight: 600; margin-top: 16px; }
.firma { margin-top: auto; padding-top: 30px; display: flex; align-items: center; gap: 18px; z-index: 3; font-weight: 700; font-size: 32px; color: #fff; }
.firma img { width: 66px; height: 66px; border-radius: 50%; border: 3px solid var(--verde); }
.ev { display: grid; grid-template-columns: 170px 1fr; gap: 26px; margin-top: 18px !important; background: rgba(24,24,27,.94); border: 2px solid #27272a; border-left: 8px solid var(--naranja); border-radius: 26px; padding: 28px 32px; margin-top: 24px; }
.ev .hora { font-family: "Anton"; font-size: 70px; color: var(--naranja); line-height: 1; }
.ev .pais { font-weight: 700; font-size: 24px; letter-spacing: .14em; color: var(--gris); }
.ev .nom { font-weight: 700; font-size: 38px; color: #fff; line-height: 1.2; margin-top: 4px; }
.ev .cif { font-size: 28px; color: #d4d4d8; margin-top: 8px; }
.alerta { margin-top: 30px; background: rgba(239,68,68,.12); border: 2px solid rgba(239,68,68,.5); border-radius: 26px; padding: 28px 32px; font-size: 32px; line-height: 1.4; color: #fecaca; font-weight: 600; }
.gigante { font-family: "Anton"; font-size: 250px; line-height: .95; color: var(--naranja); margin-top: 40px; }
.cuadro { margin-top: 40px; background: rgba(24,24,27,.94); border: 2px solid #27272a; border-radius: 30px; padding: 40px; }
.cuadro .nom { font-family: "Anton"; font-size: 84px; line-height: 1.08; color: #fff; text-transform: uppercase; }
.cuadro .cif { font-size: 36px; color: #e4e4e7; margin-top: 18px; font-weight: 600; }
.marco { flex-shrink: 0; margin: 36px auto 0; border-radius: 34px; overflow: hidden; box-shadow: 0 40px 100px rgba(0,0,0,.7), 0 0 0 3px #27272a; }
.marco img { display: block; width: 100%; }
.cta { margin-top: 40px; align-self: center; background: var(--verde); color: var(--negro); font-weight: 700; font-size: 40px; padding: 24px 46px; border-radius: 99px; box-shadow: 0 0 70px rgba(34,197,94,.4); }
.fuente { font-size: 28px; color: var(--gris); margin-top: 20px; font-weight: 600; }
"""


_FUENTES: dict[str, str] = {}


def _css() -> str:
    """El CSS con las letras metidas adentro (la página se abre sin dirección, así que no puede leer archivos)."""
    css = CSS
    for archivo in ("Anton-Regular.ttf", "Inter-Medium.otf", "Inter-SemiBold.otf", "Inter-Bold.otf"):
        if archivo not in _FUENTES:
            try:
                tipo = "font/ttf" if archivo.endswith("ttf") else "font/otf"
                _FUENTES[archivo] = _datos((LETRAS / archivo).read_bytes(), tipo)
            except OSError:
                _FUENTES[archivo] = ""
        css = css.replace(f"file://LETRAS/{archivo}", _FUENTES[archivo])
    return css


def _pagina(cuerpo: str) -> str:
    return f'<!doctype html><html lang="es"><head><meta charset="utf-8"><style>{_css()}</style></head><body>{cuerpo}</body></html>'


def _firma(usuario: str) -> str:
    return f'<div class="firma"><img src="{_logo()}"><span>{e(usuario)}</span></div>'


# ------------------------------------------------------------- plantillas

PAISES = {"USD": ("US", "EE.UU."), "EUR": ("EU", "ZONA EURO"), "AUD": ("AU", "AUSTRALIA"), "CAD": ("CA", "CANADÁ"),
          "CNY": ("CN", "CHINA"), "GBP": ("UK", "REINO UNIDO"), "JPY": ("JP", "JAPÓN")}


def html_agenda(eventos: list[dict], fecha_texto: str, usuario: str) -> str:
    filas = []
    for ev in eventos[:5]:
        bandera, pais = PAISES.get(ev.get("pais", ""), ("", ev.get("pais", "")))
        cifras = " · ".join(x for x in (f"Previsión: {ev['prevision']}" if ev.get("prevision") else "",
                                         f"Anterior: {ev['anterior']}" if ev.get("anterior") else "") if x)
        filas.append(f'<div class="ev"><div class="hora">{e(ev["hora"])}</div><div><div class="pais">{e(pais)}</div>'
                     f'<div class="nom">{e(ev["nombre"])}</div>{f"<div class=cif>{e(cifras)}</div>" if cifras else ""}</div></div>')
    return _pagina(f'''<section class="h"><div class="c">
      <span class="tag n">Agenda económica</span>
      <h1>Lo que mueve <em>el mercado hoy</em></h1>
      <div class="sub">{e(fecha_texto)} · hora Argentina</div>
      {"".join(filas)}
      <div class="alerta"><b>OJO:</b> cuando salen estos datos puede haber movimientos bruscos. Si operás, cuidá tu gestión de riesgo.</div>
    {_firma(usuario)}</div></section>''')


def html_aviso(cuando: str, hora: str, pais: str, nombre: str, prevision: str, anterior: str, usuario: str) -> str:
    bandera, nombre_pais = PAISES.get(pais, ("", pais))
    cifras = "<br>".join(x for x in (f"Previsión: <b style='color:#fff'>{e(prevision)}</b>" if prevision else "",
                                      f"Anterior: <b style='color:#fff'>{e(anterior)}</b>" if anterior else "") if x)
    return _pagina(f'''<section class="h"><div class="c">
      <span class="tag r">Atención</span>
      <div class="gigante">{e(cuando)}</div>
      <div class="sub" style="font-size:44px; color:#fff">sale un dato que mueve el mercado</div>
      <div class="cuadro"><div class="pais" style="font-weight:700; font-size:28px; letter-spacing:.14em; color:var(--gris)">{e(nombre_pais)} · {e(hora)} HS</div>
        <div class="nom" style="margin-top:12px">{e(nombre)}</div>{f'<div class="cif">{cifras}</div>' if cifras else ""}</div>
      <div class="alerta"><b>OJO:</b> puede haber más volatilidad y spreads más amplios. Si operás, cuidá tu gestión de riesgo.</div>
    {_firma(usuario)}</div></section>''')


def html_carrusel(portada: bytes, tema: str, usuario: str) -> str:
    return _pagina(f'''<section class="h"><div class="c">
      <span class="tag">Nuevo carrusel</span>
      <h1 style="font-size:96px">{e(tema)}</h1>
      <div class="marco" style="width:600px"><img src="{_datos(portada)}"></div>
      <div class="cta">Está en mi perfil ↑</div>
    {_firma(usuario)}</div></section>''')


def html_promo(imagen: bytes, titulo: str, usuario: str) -> str:
    return _pagina(f'''<section class="h"><div class="c">
      <span class="tag" style="background:#f0b90b">Publicidad</span>
      <h1 style="font-size:92px">{e(titulo)}</h1>
      <div class="marco" style="width:720px"><img src="{_datos(imagen)}"></div>
      <div class="cta">El link está en mi perfil ↑</div>
    {_firma(usuario)}</div></section>''')


def html_noticia(placa: bytes, urgente: bool, fuente: str, usuario: str) -> str:
    etiqueta = '<span class="tag r">Última hora</span>' if urgente else '<span class="tag">Noticia</span>'
    return _pagina(f'''<section class="h"><div class="c">
      {etiqueta}
      <div class="marco" style="width:912px; margin-top:44px"><img src="{_datos(placa)}"></div>
      {f'<div class="fuente">Fuente: {e(fuente)}</div>' if fuente else ""}
      <div class="cta">Toda la info en Telegram: comunidad_inversores_anuncios</div>
    {_firma(usuario)}</div></section>''')


# ------------------------------------------------------------- foto de la página

def sacar_foto(pagina_html: str, ancho: int = 1080, alto: int = 1920) -> bytes:
    """Abre la página en el navegador y devuelve la historia como JPG."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        try:
            nav = p.chromium.launch(channel="chrome")       # el Chrome que ya viene en GitHub
        except Exception:
            nav = p.chromium.launch()
        try:
            pag = nav.new_page(viewport={"width": ancho, "height": alto})
            pag.set_content(pagina_html, wait_until="load")
            pag.evaluate("document.fonts.ready")
            return pag.locator("section").first.screenshot(type="jpeg", quality=90)
        finally:
            nav.close()


def disponible() -> bool:
    try:
        import playwright  # noqa: F401
        return True
    except ImportError:
        log.warning("Historias: falta playwright (pip install playwright).")
        return False
