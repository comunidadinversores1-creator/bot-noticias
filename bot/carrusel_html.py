"""Carruseles de Instagram con el diseño nuevo (el mismo del carrusel "¿Qué es Bitcoin?"), armados en HTML.

Tres estilos, que van rotando:
  titular   -> fondo negro, letra Anton, palabra clave en verde o con banda, número grande de fondo,
               foto real o logo oficial en la portada, caja "dato" naranja, barra de progreso.
  editorial -> fondo papel claro, titular serif enorme con banda amarilla, lista con casillas (listas guardables).
  partida   -> lámina partida en dos fotos (de Eze), "El mundo lo llama X / Nosotros lo llamamos Y".

En los textos, lo que va entre *asteriscos* se resalta."""
from __future__ import annotations

import base64
import html
import re

from .historias import _logo
from .util import RAIZ

e = lambda t: html.escape(str(t or ""), quote=True)


def _datos(img: bytes | None, tipo: str = "image/jpeg") -> str:
    return f"data:{tipo};base64,{base64.b64encode(img).decode()}" if img else ""


def resaltar(texto: str, clase: str = "band") -> str:
    """'el *bot* gana' -> 'el <span class="band">bot</span> gana' (con el resto escapado)."""
    partes = re.split(r"\*([^*]+)\*", str(texto or ""))
    return "".join(f'<span class="{clase}">{e(p)}</span>' if i % 2 else e(p) for i, p in enumerate(partes))


CSS = """
.l { width: 1080px; height: 1440px; position: relative; overflow: hidden; background: var(--negro); color: var(--texto); }
.l::before { content:""; position:absolute; inset:0; background-image: linear-gradient(rgba(255,255,255,.035) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,.035) 1px, transparent 1px); background-size: 72px 72px; z-index: 0; }
.l::after { content:""; position:absolute; inset:0; background: radial-gradient(900px 700px at 85% -10%, rgba(34,197,94,.16), transparent 60%), radial-gradient(800px 600px at -10% 110%, rgba(247,147,26,.10), transparent 60%); z-index: 0; }
.pad { position: absolute; inset: 0; padding: 110px 84px 150px; z-index: 2; display: flex; flex-direction: column; }
.tag { align-self: flex-start; font-weight: 700; font-size: 26px; letter-spacing: .16em; text-transform: uppercase; color: var(--negro); background: var(--verde); padding: 10px 22px; border-radius: 99px; }
.tag.n { background: var(--naranja); } .tag.r { background: var(--rojo); color: #fff; }
h1, h2 { font-family: "Anton"; font-weight: 400; text-transform: uppercase; color: #fff; }
h1 { font-size: 140px; line-height: 1.08; }
h2 { font-size: 96px; line-height: 1.18; margin-top: 30px; }
.band { background: var(--verde); color: var(--negro); padding: 2px 14px 0; border-radius: 8px; box-decoration-break: clone; -webkit-box-decoration-break: clone; }
.verde { color: var(--verde); }
p { font-size: 40px; line-height: 1.42; margin-top: 30px; color: var(--texto); }
p b { color: #fff; }
.ghost { position: absolute; right: 50px; top: 40px; font-family: "Anton"; font-size: 300px; line-height: 1; color: rgba(255,255,255,.05); z-index: 1; }
.pie { position: absolute; left: 84px; right: 84px; bottom: 56px; display: flex; align-items: center; justify-content: space-between; z-index: 3; }
.yo { display: flex; align-items: center; gap: 16px; font-weight: 700; font-size: 28px; color: #fff; }
.yo img { width: 58px; height: 58px; border-radius: 50%; border: 3px solid var(--verde); }
.prog { display: flex; gap: 8px; }
.prog i { width: 34px; height: 8px; border-radius: 9px; background: rgba(255,255,255,.18); }
.prog i.on { background: var(--verde); }
.dato { margin-top: 40px; background: rgba(24,24,27,.92); border: 2px solid #27272a; border-left: 8px solid var(--naranja); border-radius: 26px; padding: 30px 38px; }
.dato .dt { font-weight: 700; font-size: 24px; letter-spacing: .16em; color: var(--naranja); margin-bottom: 10px; }
.dato .dc { font-size: 36px; line-height: 1.4; color: #fff; font-weight: 600; }
.desl { font-weight: 700; font-size: 26px; color: var(--verde); letter-spacing: .1em; }
.bg { position: absolute; inset: 0; z-index: 1; } .bg img { width: 100%; height: 100%; object-fit: cover; }
.bg::after { content:""; position:absolute; inset:0; background: linear-gradient(180deg, rgba(9,9,11,.1) 0%, rgba(9,9,11,.3) 35%, rgba(9,9,11,.93) 62%, var(--negro) 100%); }
.credito { position: absolute; right: 40px; top: 30px; z-index: 3; font-size: 18px; color: rgba(255,255,255,.7); text-shadow: 0 1px 4px #000; }
.palabra { align-self: flex-start; font-family: "Anton"; font-size: 180px; line-height: 1; background: var(--verde); color: var(--negro); padding: 18px 44px 8px; border-radius: 34px; margin-top: 20px; box-shadow: 0 0 90px rgba(34,197,94,.35); }
/* editorial (papel) */
.papel { background: #f4f1ea; color: #111; }
.papel::before, .papel::after { display: none; }
.papel h1, .papel h2 { font-family: "Serif"; color: #111; text-transform: uppercase; }
.papel .band { background: #ffd60a; color: #111; }
.papel p { color: #3f3f46; } .papel p b { color: #111; }
.papel .yo { color: #111; } .papel .prog i { background: rgba(0,0,0,.15); } .papel .prog i.on { background: #111; }
.papel .ghost { color: rgba(0,0,0,.06); }
.check { display: flex; gap: 22px; align-items: flex-start; font-size: 40px; font-weight: 600; color: #18181b; margin-top: 22px; line-height: 1.3; }
.check i { flex: 0 0 40px; height: 40px; border: 4px solid #111; border-radius: 6px; margin-top: 4px; }
.remate { margin-top: 40px; font-size: 46px; font-weight: 700; line-height: 1.35; color: #111; }
/* partida */
.mitad { position: absolute; left: 0; right: 0; height: 720px; overflow: hidden; z-index: 1; }
.mitad img { width: 100%; height: 100%; object-fit: cover; object-position: center 30%; filter: brightness(.8); }
.mitad::after { content:""; position:absolute; inset:0; background: linear-gradient(180deg, rgba(0,0,0,0) 35%, rgba(0,0,0,.85) 100%); }
.mitad .mt { position: absolute; left: 0; right: 0; bottom: 0; z-index: 2; padding: 0 70px 50px; font-weight: 700; font-size: 60px; line-height: 1.2; color: #fff; text-shadow: 0 4px 24px rgba(0,0,0,.8); }
.naranja { color: #fbbf24; }
.mitad.abajo .mt { padding-bottom: 150px; }
.mitad .chip { position: absolute; left: 70px; top: 50px; z-index: 2; font-weight: 700; font-size: 26px; letter-spacing: .14em; padding: 10px 22px; border-radius: 99px; }
.mitad .t .naranja { color: var(--naranja); }
"""


_FUENTES_CSS: list[str] = []


def _fuentes() -> str:
    """Las letras metidas en la página (Anton, Inter y la serif del estilo editorial)."""
    if not _FUENTES_CSS:
        partes = [":root { --verde:#22c55e; --naranja:#f7931a; --rojo:#ef4444; --negro:#09090b; --texto:#e4e4e7; --gris:#a1a1aa; }",
                  "* { box-sizing: border-box; margin: 0; padding: 0; } body { background: #000; font-family: Inter; font-weight: 500; }"]
        for familia, peso, archivo in (("Anton", 400, "Anton-Regular.ttf"), ("Inter", 500, "Inter-Medium.otf"), ("Inter", 600, "Inter-SemiBold.otf"),
                                       ("Inter", 700, "Inter-Bold.otf"), ("Serif", 400, "DMSerifDisplay-Regular.ttf")):
            try:
                datos = _datos((RAIZ / "assets" / "fuentes_letra" / archivo).read_bytes(), "font/ttf" if archivo.endswith("ttf") else "font/otf")
            except OSError:
                continue
            partes.append(f'@font-face {{ font-family: "{familia}"; font-weight: {peso}; src: url("{datos}"); }}')
        _FUENTES_CSS.append("\n".join(partes))
    return _FUENTES_CSS[0]


def _pagina(cuerpo: str) -> str:
    css = _fuentes() + CSS
    return f'<!doctype html><html lang="es"><head><meta charset="utf-8"><style>{css}</style></head><body>{cuerpo}</body></html>'


def _pie(i: int, total: int, usuario: str, primera: bool = False) -> str:
    puntos = "".join(f'<i class="{"on" if k == i else ""}"></i>' for k in range(total))
    derecha = '<div class="desl">DESLIZÁ →</div>' if primera else f'<div class="prog">{puntos}</div>'
    return f'<div class="pie"><div class="yo"><img src="{_logo()}"><div>{e(usuario)}</div></div>{derecha}</div>'


def _cierre(palabra: str, con_guia: bool, i: int, total: int, usuario: str, papel: bool = False) -> str:
    if con_guia:
        cuerpo = (f'<span class="tag">Guía gratis</span><h2 style="font-size:92px">¿Querés la guía completa?</h2>'
                  f'<div style="margin-top:40px; font-family:Anton; font-size:84px; color:{"#111" if papel else "#fff"}">COMENTÁ</div>'
                  f'<div class="palabra">{e(palabra)}</div><p>y te la mando por privado.</p>')
    else:
        cuerpo = (f'<span class="tag">¿Te sirvió?</span><h2 style="font-size:104px">Guardalo para <span class="band">tenerlo a mano</span></h2>'
                  f'<p style="font-size:44px"><b>Guardalo</b> para cuando lo necesites, <b>mandáselo</b> a alguien que le sirva y <b>seguime</b> para más contenido de trading, bots de IA y cripto, sin vender humo.</p>')
    return f'<section class="l{" papel" if papel else ""}"><div class="pad">{cuerpo}</div>{_pie(i, total, usuario)}</section>'


def credito_html(credito: str | None) -> str:
    return f'<div class="credito">{e(credito)}</div>' if credito else ""


def titular(laminas: list[dict], palabra: str, con_guia: bool, usuario: str = "@ezebagnato",
            foto: bytes | None = None, logo: bytes | None = None, credito: str = "") -> str:
    """laminas: [{'tipo':'portada','titulo','bajada'}, {'tipo':'paso','etiqueta','titulo','texto','dato'?}, ...]"""
    total = len(laminas) + 1
    secciones = []
    for i, l in enumerate(laminas):
        if l.get("tipo") == "portada":
            fondo = ""
            if foto:
                fondo = (f'<div class="bg"><img src="{_datos(foto)}"></div>'
                         f'{f"<div class=credito>{e(credito)}</div>" if credito else ""}')
            elif logo:
                fondo = (f'<div style="position:absolute; left:0; right:0; top:120px; height:640px; display:flex; align-items:center; justify-content:center; z-index:1">'
                         f'<div style="position:absolute; width:700px; height:700px; border-radius:50%; background:radial-gradient(circle, rgba(34,197,94,.30), transparent 65%)"></div>'
                         f'<img src="{_datos(logo, "image/png")}" style="position:relative; max-width:420px; max-height:420px"></div>')
            bajada = (f'<div style="font-size:44px; font-weight:600; color:#e4e4e7; margin-top:26px; line-height:1.3">{e(l["bajada"])}</div>'
                      if l.get("bajada") else "")
            cuerpo = (f'<div style="flex:1"></div><span class="tag">{e(l.get("etiqueta") or "Guía desde cero")}</span>'
                      f'<h1 style="margin-top:30px">{resaltar(l.get("titulo"), "verde")}</h1>{bajada}')
            secciones.append(f'<section class="l">{fondo}<div class="pad" style="padding-bottom:190px">{cuerpo}</div>{_pie(i, total, usuario, True)}</section>')
            continue
        dato = (f'<div class="dato"><div class="dt">{e(l.get("dato_titulo") or "DATO")}</div><div class="dc">{resaltar(l["dato"], "verde")}</div></div>'
                if l.get("dato") else "")
        cuerpo = (f'<span class="tag{" n" if i % 2 == 0 else ""}">{e(l.get("etiqueta") or f"Paso {i}")}</span>'
                  f'<h2>{resaltar(l.get("titulo"))}</h2><p>{resaltar(l.get("texto"), "verde")}</p>{dato}')
        arriba, relleno = "", ""
        if l.get("_foto"):
            # Foto real arriba, que se funde con el fondo (como la lámina 2 del carrusel de Bitcoin).
            arriba = (f'<div style="position:absolute; left:0; right:0; top:0; height:540px; z-index:1">'
                      f'<img src="{_datos(l["_foto"])}" style="width:100%; height:100%; object-fit:cover">'
                      f'<div style="position:absolute; inset:0; background:linear-gradient(180deg, rgba(9,9,11,.1) 0%, rgba(9,9,11,.55) 55%, #09090b 100%)"></div>'
                      f'{credito_html(l.get("_credito"))}</div>')
            relleno = ' style="padding-top:400px"'
        secciones.append(f'<section class="l">{arriba}<div class="ghost">{i:02d}</div><div class="pad"{relleno}>{cuerpo}</div>{_pie(i, total, usuario)}</section>')
    secciones.append(_cierre(palabra, con_guia, total - 1, total, usuario))
    return _pagina("".join(secciones))


def editorial(laminas: list[dict], palabra: str, con_guia: bool, usuario: str = "@ezebagnato", foto: bytes | None = None) -> str:
    """laminas: [{'tipo':'portada','numero','titulo','banda'}, {'tipo':'punto','titulo','lista':[...],'banda'}, ...]"""
    total = len(laminas) + 1
    secciones = []
    for i, l in enumerate(laminas):
        if l.get("tipo") == "portada":
            foto_html = (f'<div style="position:absolute; left:0; right:0; bottom:0; height:560px; z-index:1">'
                         f'<img src="{_datos(foto)}" style="width:100%; height:100%; object-fit:cover">'
                         f'<div style="position:absolute; inset:0; background:linear-gradient(180deg, #f4f1ea 0%, rgba(244,241,234,0) 35%)"></div></div>') if foto else ""
            numero = f'<div style="font-family:Serif; font-size:330px; line-height:.9; color:#111">{e(l.get("numero"))}</div>' if l.get("numero") else ""
            banda = f'<h2 style="font-size:84px; margin-top:30px">{resaltar(l["banda"])}</h2>' if l.get("banda") else ""
            cuerpo = (f'<div style="display:flex; gap:30px; align-items:flex-start">{numero}'
                      f'<h1 style="font-size:{104 if numero else 130}px; line-height:1.14; margin-top:24px">{resaltar(l.get("titulo"))}</h1></div>{banda}')
            secciones.append(f'<section class="l papel">{foto_html}<div class="pad">{cuerpo}</div>{_pie(i, total, usuario, True)}</section>')
            continue
        lista = "".join(f'<div class="check"><i></i><span>{resaltar(x)}</span></div>' for x in (l.get("lista") or [])[:4])
        remate = f'<div class="remate">{resaltar(l["banda"])}</div>' if l.get("banda") else ""
        texto = f'<p>{resaltar(l["texto"])}</p>' if l.get("texto") else ""
        cuerpo = (f'<div style="flex:1"></div><h2 style="font-size:104px; line-height:1.08">{resaltar(l.get("titulo"))}</h2>'
                  f'<div style="margin-top:30px">{lista}</div>{remate}{texto}<div style="flex:1"></div>')
        secciones.append(f'<section class="l papel"><div class="ghost">{i:02d}</div><div class="pad">{cuerpo}</div>{_pie(i, total, usuario)}</section>')
    secciones.append(_cierre(palabra, con_guia, total - 1, total, usuario, papel=True))
    return _pagina("".join(secciones))


def partida(laminas: list[dict], palabra: str, con_guia: bool, fotos: list[bytes], usuario: str = "@ezebagnato") -> str:
    """laminas: [{'arriba': 'El mundo lo llama *apostar*', 'abajo': 'Nosotros lo llamamos *trading*'}, ...]"""
    total = len(laminas) + 1
    secciones = []
    for i, l in enumerate(laminas):
        def mitad(texto: str, k: int, arriba: bool) -> str:
            img = f'<img src="{_datos(fotos[k % len(fotos)])}">' if fotos else '<div style="width:100%;height:100%;background:#18181b"></div>'
            chip = ('<div class="chip" style="background:#ef4444; color:#fff">✕ LO QUE HACE LA MAYORÍA</div>' if arriba
                    else '<div class="chip" style="background:#22c55e; color:#09090b">✓ LO QUE HACE EL QUE DURA</div>')
            return (f'<div class="mitad{"" if arriba else " abajo"}" style="{"top:0" if arriba else "bottom:0"}">{img}{chip}'
                    f'<div class="mt">{resaltar(texto, "naranja")}</div></div>')
        secciones.append(f'<section class="l">{mitad(l.get("arriba"), 2 * i, True)}{mitad(l.get("abajo"), 2 * i + 1, False)}'
                         f'<div style="position:absolute; left:0; right:0; top:716px; height:8px; background:#000; z-index:2"></div>'
                         f'{_pie(i, total, usuario, i == 0)}</section>')
    secciones.append(_cierre(palabra, con_guia, total - 1, total, usuario))
    return _pagina("".join(secciones))


def sacar_fotos(pagina_html: str) -> list[bytes]:
    """Una foto JPG por lámina (1080x1440)."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        try:
            nav = p.chromium.launch(channel="chrome")
        except Exception:
            nav = p.chromium.launch()
        try:
            pag = nav.new_page(viewport={"width": 1080, "height": 1440})
            pag.set_content(pagina_html, wait_until="load")
            pag.evaluate("document.fonts.ready")
            return [s.screenshot(type="jpeg", quality=92) for s in pag.locator("section.l").all()]
        finally:
            nav.close()
