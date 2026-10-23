"""Diseños para Instagram: carruseles (4:5, 1080x1350) e historias (9:16, 1080x1920).

Estilo de Eze: fondo oscuro, dorado del aro de su logo, letras blancas grandes, su cara en círculo.
Sin emojis dentro de las imágenes (la letra no los tiene): los íconos se dibujan."""
from __future__ import annotations

import io
import re
from datetime import datetime

from PIL import Image, ImageDraw, ImageFilter

from .imagen import _logo_propio, _partir, color, letra

ANCHO, ALTO_CARRUSEL, ALTO_HISTORIA = 1080, 1350, 1920
MARGEN = 90
FONDO = (11, 15, 20)
BLANCO = (255, 255, 255, 255)
GRIS = (170, 178, 189, 255)
DORADO = color("#F5B50A")
ROJO = color("#EF4444")


# ------------------------------------------------------------- piezas

def _fondo(alto: int, brillo_y: int | None = None) -> Image.Image:
    base = Image.new("RGB", (ANCHO, alto), FONDO)
    capa = Image.new("RGB", base.size, FONDO)
    y = brillo_y if brillo_y is not None else -300
    ImageDraw.Draw(capa).ellipse((380, y, 1500, y + 900), fill=(120, 86, 8))
    base = Image.blend(base, capa.filter(ImageFilter.GaussianBlur(220)), 0.55)
    # Una grilla muy suave, como de gráfico de trading.
    d = ImageDraw.Draw(base, "RGBA")
    for x in range(0, ANCHO, 90):
        d.line((x, 0, x, alto), fill=(255, 255, 255, 7))
    for yy in range(0, alto, 90):
        d.line((0, yy, ANCHO, yy), fill=(255, 255, 255, 7))
    return base


def _jpg(img: Image.Image) -> bytes:
    b = io.BytesIO()
    img.convert("RGB").save(b, "JPEG", quality=92, optimize=True)
    return b.getvalue()


def _texto(d: ImageDraw.ImageDraw, x: int, y: int, texto: str, estilo: str, tam: int, ancho: int, tinta=BLANCO,
           interlineado: float = 1.25, max_lineas: int = 20) -> int:
    """Escribe un párrafo que se parte solo en renglones. Devuelve la y donde terminó."""
    fuente = letra(estilo, tam)
    for parrafo in str(texto).split("\n"):
        if not parrafo.strip():
            y += round(tam * 0.6)
            continue
        for linea in _partir(parrafo.strip(), fuente, ancho)[:max_lineas]:
            d.text((x, y), linea, font=fuente, fill=tinta)
            y += round(tam * interlineado)
    return y


def _alto_texto(texto: str, estilo: str, tam: int, ancho: int, interlineado: float = 1.25) -> int:
    fuente = letra(estilo, tam)
    alto = 0
    for parrafo in str(texto).split("\n"):
        if not parrafo.strip():
            alto += round(tam * 0.6)
            continue
        alto += len(_partir(parrafo.strip(), fuente, ancho)) * round(tam * interlineado)
    return alto


def _que_entre(texto: str, estilo: str, ancho: int, alto: int, tam_max: int, tam_min: int, interlineado: float = 1.25) -> int:
    for tam in range(tam_max, tam_min - 1, -2):
        if _alto_texto(texto, estilo, tam, ancho, interlineado) <= alto:
            return tam
    return tam_min


def _titulo_con_dorado(d: ImageDraw.ImageDraw, x: int, y: int, texto: str, tam: int, ancho: int, interlineado: float = 1.08) -> int:
    """Título grande: lo que va entre *asteriscos* sale en dorado."""
    fuente = letra("titulo", tam)
    resaltadas = {p.lower().strip(".,:;¿?¡!") for p in re.findall(r"\*([^*]+)\*", texto) for p in p.split()}
    limpio = texto.replace("*", "")
    espacio = fuente.getlength(" ")
    for linea in _partir(limpio, fuente, ancho):
        cx = x
        for palabra in linea.split(" "):
            tinta = DORADO if palabra.lower().strip(".,:;¿?¡!") in resaltadas else BLANCO
            d.text((cx, y), palabra, font=fuente, fill=tinta)
            cx += fuente.getlength(palabra) + espacio
        y += round(tam * interlineado)
    return y


def _alto_titulo(texto: str, tam: int, ancho: int, interlineado: float = 1.08) -> int:
    return len(_partir(texto.replace("*", ""), letra("titulo", tam), ancho)) * round(tam * interlineado)


def _pastilla(d: ImageDraw.ImageDraw, x: int, y: int, texto: str, fondo=DORADO, tinta=(8, 11, 15, 255), tam: int = 30) -> int:
    fuente = letra("negrita", tam)
    ancho = round(fuente.getlength(texto)) + 48
    alto = round(tam * 1.9)
    d.rounded_rectangle((x, y, x + ancho, y + alto), radius=alto // 2, fill=fondo)
    d.text((x + 24, y + alto / 2 + 1), texto, font=fuente, fill=tinta, anchor="lm")
    return ancho


def _firma(base: Image.Image, d: ImageDraw.ImageDraw, y: int, usuario: str, nombre: str = "Ezequiel Bagnato") -> None:
    """Abajo a la izquierda: la cara de Eze en círculo, su nombre y su usuario."""
    logo = _logo_propio(96)
    x = MARGEN
    if logo is not None:
        base.paste(logo, (x, y), logo)
        x += 96 + 22
    d.text((x, y + 14), nombre, font=letra("negrita", 34), fill=BLANCO)
    d.text((x, y + 56), usuario, font=letra("normal", 28), fill=GRIS)


def _flecha(d: ImageDraw.ImageDraw, x: int, y: int, texto: str = "Deslizá") -> None:
    fuente = letra("media", 30)
    ancho = fuente.getlength(texto)
    d.text((x - ancho - 70, y), texto, font=fuente, fill=DORADO, anchor="lm")
    d.line((x - 56, y, x, y), fill=DORADO, width=5)
    d.polygon([(x + 4, y), (x - 16, y - 13), (x - 16, y + 13)], fill=DORADO)


def _numero_pagina(d: ImageDraw.ImageDraw, actual: int, total: int, alto: int) -> None:
    fuente = letra("media", 26)
    d.text((ANCHO - MARGEN, 70), f"{actual}/{total}", font=fuente, fill=GRIS, anchor="ra")


# ------------------------------------------------------------- carrusel

def carrusel(contenido: dict, usuario: str = "@ezebagnato", etiqueta: str = "APRENDÉ") -> list[bytes]:
    """contenido = {"titulo": "¿Qué es un *stop loss*?", "bajada": "...",
                    "placas": [{"titulo": "...", "texto": "..."}, ...], "cierre": "..."}
    Devuelve las imágenes en orden: portada, una por placa y el cierre."""
    placas = [p for p in contenido.get("placas", []) if p.get("titulo") or p.get("texto")][:8]
    total = len(placas) + 2
    salida = []

    # Portada
    img = _fondo(ALTO_CARRUSEL, brillo_y=-200)
    d = ImageDraw.Draw(img, "RGBA")
    _pastilla(d, MARGEN, 110, etiqueta)
    ancho = ANCHO - 2 * MARGEN
    titulo = str(contenido.get("titulo", ""))
    tam = 132
    while tam > 70 and _alto_titulo(titulo, tam, ancho) > 560:
        tam -= 4
    y = 300 + max(0, (560 - _alto_titulo(titulo, tam, ancho)) // 2)
    y = _titulo_con_dorado(d, MARGEN, y, titulo, tam, ancho)
    if contenido.get("bajada"):
        d.line((MARGEN, y + 30, MARGEN + 120, y + 30), fill=DORADO, width=6)
        _texto(d, MARGEN, y + 70, str(contenido["bajada"]), "media", 40, ancho, GRIS, max_lineas=3)
    _firma(img, d, ALTO_CARRUSEL - 200, usuario)
    _flecha(d, ANCHO - MARGEN, ALTO_CARRUSEL - 150)
    salida.append(_jpg(img))

    # Placas
    for i, p in enumerate(placas, start=1):
        img = _fondo(ALTO_CARRUSEL, brillo_y=-500 + (i % 3) * 250)
        d = ImageDraw.Draw(img, "RGBA")
        _numero_pagina(d, i + 1, total, ALTO_CARRUSEL)
        d.text((MARGEN, 130), f"{i:02d}", font=letra("titulo", 130), fill=DORADO)
        y = 320
        if p.get("titulo"):
            tam_t = 70
            while tam_t > 46 and _alto_titulo(str(p["titulo"]), tam_t, ancho) > 260:
                tam_t -= 2
            y = _titulo_con_dorado(d, MARGEN, y, str(p["titulo"]), tam_t, ancho) + 40
        cuerpo = str(p.get("texto", ""))
        if cuerpo:
            disponible = ALTO_CARRUSEL - 200 - y
            tam_c = _que_entre(cuerpo, "normal", ancho, disponible, 54, 30, 1.35)
            _texto(d, MARGEN, y, cuerpo, "normal", tam_c, ancho, (225, 230, 236, 255), 1.35)
        d.text((MARGEN, ALTO_CARRUSEL - 110), usuario, font=letra("media", 28), fill=GRIS)
        if i < len(placas):
            _flecha(d, ANCHO - MARGEN, ALTO_CARRUSEL - 95)
        salida.append(_jpg(img))

    # Cierre
    img = _fondo(ALTO_CARRUSEL, brillo_y=300)
    d = ImageDraw.Draw(img, "RGBA")
    _numero_pagina(d, total, total, ALTO_CARRUSEL)
    logo = _logo_propio(260)
    if logo is not None:
        img.paste(logo, ((ANCHO - 260) // 2, 230), logo)
    cierre = str(contenido.get("cierre") or "Guardalo para tenerlo a mano y compartilo con alguien que esté empezando.")
    y = 560
    y = _titulo_con_dorado(d, MARGEN, y, "*Seguime* para aprender trading, bots e IA sin humo", 64, ancho) + 40
    _texto(d, MARGEN, y, cierre, "media", 38, ancho, GRIS)
    _pastilla(d, MARGEN, ALTO_CARRUSEL - 230, f"Seguir a {usuario}", tam=34)
    salida.append(_jpg(img))
    return salida


def resumen_semanal(noticias: list[dict], desde: datetime, hasta: datetime, usuario: str = "@comunidad_inversores") -> list[bytes]:
    """Carrusel 'Las N noticias que movieron el mercado': noticias = [{"titulo", "texto", "categoria"}]."""
    n = len(noticias)
    meses = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]
    fechas = f"{desde.day} al {hasta.day} de {meses[hasta.month - 1]}"
    contenido = {
        "titulo": f"Las *{n}* noticias que movieron el mercado esta semana",
        "bajada": f"Resumen del {fechas}",
        "placas": [{"titulo": x["titulo"], "texto": x.get("texto", "")} for x in noticias],
        "cierre": "Todos los días publico las noticias que mueven el mercado, apenas salen.",
    }
    return carrusel(contenido, usuario, etiqueta="RESUMEN SEMANAL")


# ------------------------------------------------------------- historias

def _historia_base(etiqueta: str, tinta=DORADO) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = _fondo(ALTO_HISTORIA, brillo_y=-250)
    d = ImageDraw.Draw(img, "RGBA")
    # Instagram tapa los primeros ~250 px (barra de progreso y nombre) y los últimos ~250 (responder).
    _pastilla(d, MARGEN, 270, etiqueta, fondo=tinta, tam=34)
    return img, d


LIMITE_HISTORIA = ALTO_HISTORIA - 330     # más abajo, Instagram tapa con la barra de "Enviar mensaje"


def _pie_historia(img: Image.Image, d: ImageDraw.ImageDraw, usuario: str) -> None:
    """El usuario chiquito y centrado abajo (arriba Instagram ya muestra la foto y el nombre)."""
    d.text((ANCHO // 2, LIMITE_HISTORIA + 40), usuario, font=letra("media", 32), fill=GRIS, anchor="mm")


def historia_agenda(eventos: list[dict], fecha: datetime, usuario: str) -> bytes:
    """eventos = [{"hora": "09:30", "pais": "EE.UU.", "nombre": "inflación (IPC)", "cifras": "Previsión 0,3% · Anterior 0,2%"}]"""
    img, d = _historia_base("AGENDA ECONÓMICA")
    dias = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
    meses = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
    ancho = ANCHO - 2 * MARGEN
    y = _titulo_con_dorado(d, MARGEN, 400, f"Los datos que mueven el mercado *hoy*", 84, ancho)
    d.text((MARGEN, y + 20), f"{dias[fecha.weekday()].capitalize()} {fecha.day} de {meses[fecha.month - 1]} · hora Argentina",
           font=letra("media", 34), fill=GRIS)
    y += 110
    lugar = LIMITE_HISTORIA - 230 - y                 # lo que queda arriba del aviso de volatilidad
    entran, usado = [], 0
    for e in eventos:
        alto = (150 if e.get("cifras") else 110) + 22
        if usado + alto > lugar:
            break
        entran.append(e)
        usado += alto
    for e in entran:
        alto = 150 if e.get("cifras") else 110
        d.rounded_rectangle((MARGEN, y, ANCHO - MARGEN, y + alto), radius=24, fill=(255, 255, 255, 14), outline=(255, 255, 255, 30), width=2)
        d.text((MARGEN + 34, y + 30), e["hora"], font=letra("titulo", 50), fill=DORADO)
        x = MARGEN + 230
        d.text((x, y + 28), e["pais"].upper(), font=letra("negrita", 26), fill=GRIS)
        nombre = e["nombre"][0].upper() + e["nombre"][1:]
        fuente = letra("negrita", 40)
        while fuente.getlength(nombre) > ANCHO - MARGEN - 30 - x and len(nombre) > 10:
            nombre = nombre[:-2].rstrip() + "…"
        d.text((x, y + 60), nombre, font=fuente, fill=BLANCO)
        if e.get("cifras"):
            d.text((x, y + 110), e["cifras"], font=letra("normal", 28), fill=GRIS)
        y += alto + 22
    y = max(y + 20, LIMITE_HISTORIA - 200)
    d.rounded_rectangle((MARGEN, y, ANCHO - MARGEN, y + 170), radius=24, fill=(239, 68, 68, 40), outline=ROJO, width=3)
    _texto(d, MARGEN + 36, y + 30, "Cuando salen estos datos puede haber movimientos bruscos. Si operás, cuidá tu gestión de riesgo.",
           "media", 34, ancho - 72, BLANCO)
    _pie_historia(img, d, usuario)
    return _jpg(img)


def historia_aviso(hora: str, pais: str, nombre: str, cifras: str, cuando: str, usuario: str) -> bytes:
    img, d = _historia_base(cuando.upper(), tinta=ROJO)
    ancho = ANCHO - 2 * MARGEN
    d.text((MARGEN, 420), pais.upper(), font=letra("negrita", 40), fill=GRIS)
    y = _titulo_con_dorado(d, MARGEN, 480, f"Sale el dato de *{nombre}*", 100, ancho)
    y += 40
    d.text((MARGEN, y), hora, font=letra("titulo", 220), fill=DORADO)
    d.text((MARGEN, y + 250), "hora Argentina", font=letra("media", 36), fill=GRIS)
    y += 340
    if cifras:
        _texto(d, MARGEN, y, cifras, "negrita", 44, ancho)
        y += 120
    d.rounded_rectangle((MARGEN, y, ANCHO - MARGEN, y + 210), radius=24, fill=(239, 68, 68, 40), outline=ROJO, width=3)
    _texto(d, MARGEN + 36, y + 32, "Puede haber más volatilidad y spreads más amplios en ese momento. Si operás, cuidá tu gestión de riesgo.",
           "media", 36, ancho - 72, BLANCO)
    _pie_historia(img, d, usuario)
    return _jpg(img)


def historia_noticia(titulo: str, texto: str, categoria: str, usuario: str, urgente: bool = False) -> bytes:
    img, d = _historia_base("ÚLTIMA HORA" if urgente else categoria.upper(), tinta=ROJO if urgente else DORADO)
    ancho = ANCHO - 2 * MARGEN
    tam = 104
    while tam > 64 and _alto_titulo(titulo, tam, ancho) > 620:
        tam -= 4
    y = _titulo_con_dorado(d, MARGEN, 420, titulo, tam, ancho) + 50
    d.line((MARGEN, y, MARGEN + 120, y), fill=DORADO, width=6)
    y += 50
    disponible = LIMITE_HISTORIA - 40 - y
    tam_c = _que_entre(texto, "normal", ancho, disponible, 44, 32, 1.35)
    _texto(d, MARGEN, y, texto, "normal", tam_c, ancho, (225, 230, 236, 255), 1.35)
    _pie_historia(img, d, usuario)
    return _jpg(img)


def historia_promo(imagen: Image.Image, usuario: str, texto_abajo: str = "El link está en mi perfil") -> bytes:
    """Una promo con su flyer: el flyer al centro, fondo de la marca y el aviso de dónde está el link."""
    img, d = _historia_base("PROMO")
    flyer = imagen.convert("RGB")
    escala = min((ANCHO - 2 * 60) / flyer.width, (LIMITE_HISTORIA - 380 - 160) / flyer.height)
    flyer = flyer.resize((round(flyer.width * escala), round(flyer.height * escala)), Image.LANCZOS)
    x = (ANCHO - flyer.width) // 2
    img.paste(flyer, (x, 380))
    y = 380 + flyer.height + 50
    _pastilla(d, MARGEN, y, texto_abajo, tam=36)
    _pie_historia(img, d, usuario)
    return _jpg(img)
