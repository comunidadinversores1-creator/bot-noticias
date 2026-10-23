"""La imagen cuadrada (1:1) de cada publicación.

De dónde sale la imagen, en este orden:
 1) LOGO OFICIAL guardado en assets/marcas (Binance, Bitget, Bitcoin, Nvidia...) cuando la
    noticia es sobre una empresa, un exchange o una moneda.
 2) FOTO REAL de Wikimedia Commons (licencia libre: se puede usar sin que X o Telegram la
    bajen por derechos de autor). Siempre deja el crédito del autor en la imagen.
 3) Opcional y pago: imagen generada con IA (nunca personas ni hechos reales, y sale avisada).
 4) Placa solo de texto.

Después arma la placa de 1080x1080 con categoría, titular, fuente, fecha y la marca de Eze."""
from __future__ import annotations

import base64
import io
import json
import re
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache

import requests
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

from .util import AGENTE_WIKI, ESPACIO_DURO, RAIZ, fecha_corta, limpiar_html, log, normalizar

LADO = 1080
MARGEN = 64
LETRAS = RAIZ / "assets" / "fuentes_letra"
MARCAS = RAIZ / "assets" / "marcas"
COMMONS = "https://commons.wikimedia.org/w/api.php"
WIKIPEDIA = "https://en.wikipedia.org/w/api.php"
INTERACCIONES = "https://generativelanguage.googleapis.com/v1beta/interactions"
DATOS_FOTO = "LicenseShortName|Artist|ImageDescription|DateTimeOriginal|DateTime"

# Archivos que no son una foto del protagonista (se descartan salvo que se pida un logo).
NO_ES_FOTO = re.compile(
    r"(?i)\b(logo|logotipo|signature|firma|autograph|map|mapa|locator|coat of arms|escudo|seal|sello|flag|bandera|"
    r"diagram|chart|graph|grafico|icon|icono|screenshot|captura|cartoon|caricature|caricatura|drawing|dibujo|"
    r"painting|pintura|engraving|grabado|illustration|ilustracion|stamp|estampilla|poster|book cover|cover|"
    r"letter|carta|document|documento|manuscript|tomb|grave|bust|statue|estatua|wordmark|banner|infographic)\b"
)
PIDE_LOGO = re.compile(r"(?i)\b(logo|logotipo|logotype)\b")


@dataclass
class Foto:
    titulo: str
    url: str
    ancho: int
    alto: int
    mime: str
    licencia: str
    autor: str
    descripcion: str
    anio: str
    es_logo: bool = False
    imagen: Image.Image | None = None
    origen: str = "wikimedia"        # 'wikimedia', 'marca' (logo guardado) o 'ia' (generada)

    def credito(self) -> str:
        if self.origen == "marca":
            return ""
        if self.origen == "ia":
            return "Imagen generada con IA"
        autor = re.sub(r"\s+", " ", self.autor).strip()
        if len(autor) > 38:
            autor = autor[:36].rstrip() + "…"
        partes = ["Foto: " + autor if autor else "Foto", self.licencia, "Wikimedia Commons"]
        return " · ".join(p for p in partes if p)

    def descripcion_corta(self) -> str:
        if self.origen == "marca":
            return f"LOGO OFICIAL de {self.descripcion}"
        nombre = re.sub(r"(?i)^file:|\.(jpe?g|png|webp|svg|tiff?)$", "", self.titulo).replace("_", " ")
        return f"{nombre} | {self.descripcion[:110]} | {self.anio or 's/f'}"


# ------------------------------------------------------- logos guardados

@lru_cache(maxsize=1)
def _indice_marcas() -> dict:
    try:
        return json.loads((MARCAS / "indice.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def logo_guardado(consulta: str) -> Foto | None:
    """Si la búsqueda pide el logo de una marca que está en assets/marcas, lo devuelve (sin internet)."""
    frase = normalizar(re.sub(r"(?i)\b(logo|logotipo|logotype|official|oficial|icon|symbol|brand)\b", " ", consulta))
    if not frase:
        return None
    indice = _indice_marcas()
    dato = indice.get(frase)
    if dato is None:
        for alias, d in indice.items():          # 'binance exchange' contiene a 'binance'
            if len(alias) >= 4 and f" {alias} " in f" {frase} ":
                dato = d
                break
    if dato is None:
        return None
    try:
        img = Image.open(MARCAS / dato["archivo"]).convert("RGBA")
    except (OSError, KeyError):
        return None
    return Foto(
        titulo=f"marca:{dato['archivo']}", url="", ancho=img.width, alto=img.height, mime="image/png",
        licencia="", autor="", descripcion=str(dato.get("descripcion", "")), anio="",
        es_logo=True, imagen=img, origen="marca",
    )


# ------------------------------------------------------------ buscar foto

def _api(url: str, parametros: dict) -> dict:
    r = requests.get(
        url,
        params={**parametros, "format": "json", "formatversion": "2"},
        headers={"User-Agent": AGENTE_WIKI},
        timeout=(6, 15),
    )
    r.raise_for_status()
    return r.json()


def licencia_libre(licencia: str) -> bool:
    """True solo para licencias que permiten usar y modificar la foto en una cuenta comercial."""
    l = (licencia or "").lower().strip()
    if not l or "fair use" in l or "non-free" in l or "copyright" in l:
        return False
    if re.search(r"\b(nc|nd)\b", l.replace("-", " ")):
        return False
    return bool(re.match(r"(cc0|cc[ -]by|cc[ -]zero|public domain|pd\b|pdm|no restrictions|attribution|ogl|fal)", l))


def _a_foto(pagina: dict, es_logo: bool) -> Foto | None:
    info = (pagina.get("imageinfo") or [None])[0]
    if not info:
        return None
    meta = info.get("extmetadata") or {}
    dato = lambda clave, maximo=160: limpiar_html((meta.get(clave) or {}).get("value", ""), maximo)
    anio = re.search(r"(19|20)\d\d", dato("DateTimeOriginal") or dato("DateTime"))
    return Foto(
        titulo=pagina.get("title", ""),
        url=info.get("thumburl") or info.get("url") or "",
        ancho=int(info.get("width") or 0),
        alto=int(info.get("height") or 0),
        mime=info.get("mime", ""),
        licencia=dato("LicenseShortName", 40),
        autor=dato("Artist", 90),
        descripcion=dato("ImageDescription", 200),
        anio=anio.group(0) if anio else "",
        es_logo=es_logo,
    )


def sirve(f: Foto) -> bool:
    if f.origen != "wikimedia":
        return f.imagen is not None
    if not f.url or not licencia_libre(f.licencia):
        return False
    if f.es_logo:
        return f.mime in ("image/svg+xml", "image/png", "image/jpeg") and f.ancho >= 200
    if f.mime not in ("image/jpeg", "image/png", "image/webp"):
        return False
    if f.ancho < 800 or f.alto < 600 or not (0.5 <= f.ancho / max(f.alto, 1) <= 2.6):
        return False
    return not NO_ES_FOTO.search(f.titulo.replace("_", " "))


def _portada_wikipedia(consulta: str) -> str | None:
    """La foto principal del artículo de Wikipedia de esa persona, empresa o lugar (solo si es libre)."""
    datos = _api(WIKIPEDIA, {
        "action": "query", "generator": "search", "gsrsearch": consulta, "gsrlimit": 1, "gsrnamespace": 0,
        "prop": "pageimages", "piprop": "name", "pilicense": "free",
    })
    paginas = (datos.get("query") or {}).get("pages") or []
    nombre = paginas[0].get("pageimage") if paginas else None
    return f"File:{nombre}" if nombre else None


def _datos_de(titulos: list[str], es_logo: bool) -> list[Foto]:
    datos = _api(COMMONS, {
        "action": "query", "titles": "|".join(titulos), "prop": "imageinfo",
        "iiprop": "url|size|mime|extmetadata", "iiurlwidth": 1280, "iiextmetadatafilter": DATOS_FOTO,
    })
    paginas = (datos.get("query") or {}).get("pages") or []
    return [f for f in (_a_foto(p, es_logo) for p in paginas if not p.get("missing")) if f]


def _buscar_commons(consulta: str, es_logo: bool) -> list[Foto]:
    datos = _api(COMMONS, {
        "action": "query", "generator": "search", "gsrnamespace": 6, "gsrlimit": 12,
        "gsrsearch": consulta if es_logo else f"filetype:bitmap {consulta}",
        "prop": "imageinfo", "iiprop": "url|size|mime|extmetadata", "iiurlwidth": 1280,
        "iiextmetadatafilter": DATOS_FOTO,
    })
    paginas = sorted((datos.get("query") or {}).get("pages") or [], key=lambda p: p.get("index", 99))
    return [f for f in (_a_foto(p, es_logo) for p in paginas) if f]


def candidatas(consulta: str) -> list[Foto]:
    """Imágenes posibles para una búsqueda, de la más precisa a la menos."""
    es_logo = bool(PIDE_LOGO.search(consulta))
    if es_logo:
        guardado = logo_guardado(consulta)
        if guardado is not None:
            return [guardado]                   # el logo oficial ya está en el bot: no hace falta buscar
    encontradas: list[Foto] = []
    try:
        if not es_logo:
            portada = _portada_wikipedia(consulta)
            if portada:
                encontradas += _datos_de([portada], False)
        encontradas += _buscar_commons(consulta, es_logo)
    except (requests.RequestException, ValueError, KeyError, TypeError) as error:
        log.warning("  búsqueda de foto '%s' falló: %s", consulta, type(error).__name__)
    unicas: list[Foto] = []
    for f in encontradas:
        if sirve(f) and all(u.titulo != f.titulo for u in unicas):
            unicas.append(f)
    return unicas


def _descargar(f: Foto) -> Image.Image | None:
    if f.imagen is not None:
        return f.imagen
    try:
        r = requests.get(f.url, headers={"User-Agent": AGENTE_WIKI}, timeout=(6, 25))
        if r.status_code != 200 or len(r.content) < 800:
            log.warning("  no pude bajar la foto (%s): %s", r.status_code, f.titulo[:60])
            return None
        img = Image.open(io.BytesIO(r.content))
        img.load()
    except (requests.RequestException, OSError, ValueError, Image.DecompressionBombError) as error:
        log.warning("  foto ilegible (%s): %s", type(error).__name__, f.titulo[:60])
        return None
    if f.es_logo:
        return img.convert("RGBA")
    img = ImageOps.exif_transpose(img).convert("RGB")
    return img if min(img.size) >= 500 else None


def buscar_foto(busquedas: list[str], titular: str, ya_usada, elegir=None) -> Foto | None:
    """Devuelve una imagen real para la noticia, o None si no hay ninguna que tenga relación.

    ya_usada(titulo) -> bool  : para no repetir siempre la misma foto.
    elegir(titular, opciones) : la IA elige entre las candidatas (-1 = ninguna sirve)."""
    opciones: list[Foto] = []
    for consulta in busquedas[:4]:
        encontradas = candidatas(consulta)
        nuevas = [f for f in encontradas if f.origen == "marca" or not ya_usada(f.titulo)] or encontradas
        for f in nuevas[:3]:
            if all(o.titulo != f.titulo for o in opciones):
                opciones.append(f)
    opciones = opciones[:8]
    if not opciones:
        return None
    indice = elegir(titular, [f.descripcion_corta() for f in opciones]) if elegir else None
    if indice == -1:
        log.info("  la IA no encontró ninguna imagen con relación directa")
        return None
    orden = opciones if indice is None else [opciones[indice]] + [f for i, f in enumerate(opciones) if i != indice]
    for f in orden[: (1 if indice is not None else 3)]:
        img = _descargar(f)
        if img is not None:
            f.imagen = img
            return f
    return None


# ------------------------------------------------- imagen generada con IA

def _primera_imagen(nodo) -> str | None:
    """Busca la imagen (en base64) dentro de la respuesta, esté donde esté."""
    if isinstance(nodo, dict):
        dato = nodo.get("data")
        tipo = str(nodo.get("mime_type") or nodo.get("mimeType") or "")
        if isinstance(dato, str) and len(dato) > 200 and (nodo.get("type") == "image" or tipo.startswith("image/")):
            return dato
        for valor in nodo.values():
            encontrada = _primera_imagen(valor)
            if encontrada:
                return encontrada
    elif isinstance(nodo, list):
        for valor in nodo:
            encontrada = _primera_imagen(valor)
            if encontrada:
                return encontrada
    return None


def generar_con_ia(escena: str, clave: str, modelo: str) -> Foto | None:
    """Plan C: una imagen genérica hecha con IA. Nunca personas reales, marcas ni texto."""
    if not escena or not clave:
        return None
    pedido = (
        "Editorial news photograph, photorealistic, natural light, serious tone. No text, no letters, no numbers, "
        "no logos, no watermarks, no recognizable real people, no politicians, no celebrities. Scene: " + escena
    )
    try:
        r = requests.post(
            INTERACCIONES,
            headers={"x-goog-api-key": clave, "Content-Type": "application/json"},
            json={
                "model": modelo,
                "input": [{"type": "text", "text": pedido}],
                "response_format": {"type": "image", "mime_type": "image/jpeg", "aspect_ratio": "1:1", "image_size": "1K"},
            },
            timeout=90,
        )
        if r.status_code != 200:
            log.warning("  la IA de imágenes respondió %s (¿la clave tiene pago habilitado?)", r.status_code)
            return None
        dato = _primera_imagen(r.json())
        if not dato:
            return None
        img = Image.open(io.BytesIO(base64.b64decode(dato))).convert("RGB")
    except (requests.RequestException, OSError, ValueError) as error:
        log.warning("  no pude generar la imagen con IA (%s)", type(error).__name__)
        return None
    if min(img.size) < 500:
        return None
    return Foto(
        titulo="ia:" + escena[:60], url="", ancho=img.width, alto=img.height, mime="image/jpeg",
        licencia="", autor="", descripcion=escena, anio="", imagen=img, origen="ia",
    )


# ------------------------------------------------------------ dibujar

@lru_cache(maxsize=64)
def letra(estilo: str, tam: int) -> ImageFont.FreeTypeFont:
    archivo = {
        "titulo": "InterDisplay-Black.otf",
        "fuerte": "InterDisplay-ExtraBold.otf",
        "negrita": "Inter-Bold.otf",
        "media": "Inter-SemiBold.otf",
        "normal": "Inter-Medium.otf",
    }[estilo]
    return ImageFont.truetype(str(LETRAS / archivo), tam)


def color(hexa: str, alfa: int = 255) -> tuple[int, int, int, int]:
    h = str(hexa).lstrip("#")
    if len(h) != 6:
        h = "3B82F6"
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), alfa


def cubrir(img: Image.Image, ancho: int, alto: int) -> Image.Image:
    """Agranda y recorta la foto para llenar el espacio. En fotos verticales cuida la parte de arriba (caras)."""
    escala = max(ancho / img.width, alto / img.height)
    nueva = img.resize((max(ancho, round(img.width * escala)), max(alto, round(img.height * escala))), Image.LANCZOS)
    sobra_x, sobra_y = nueva.width - ancho, nueva.height - alto
    # Las caras suelen estar en la parte de arriba: al recortar se cuida esa zona.
    ancla_y = 0.12 if img.height > img.width * 1.1 else 0.28
    x, y = sobra_x // 2, round(sobra_y * ancla_y)
    return nueva.crop((x, y, x + ancho, y + alto))


def oscurecer(base: Image.Image, y0: int, y1: int, alfa_max: int, hacia_abajo: bool = True) -> None:
    """Degradado negro suave entre y0 e y1 para que el texto se lea sobre cualquier foto."""
    mascara = Image.new("L", (1, base.height), 0)
    pixeles = mascara.load()
    for y in range(base.height):
        t = (y - y0) / max(1, y1 - y0)
        t = min(1.0, max(0.0, t if hacia_abajo else 1 - t))
        pixeles[0, y] = round(alfa_max * (t * t * (3 - 2 * t)))
    base.paste(Image.new("RGB", base.size, (5, 8, 12)), (0, 0), mascara.resize(base.size))


def _brillo(base: Image.Image, tinta, fuerza: float = 0.55) -> Image.Image:
    """Un resplandor del color de acento arriba a la derecha, para las placas sin foto."""
    capa = Image.new("RGB", base.size, base.getpixel((0, 0)))
    ImageDraw.Draw(capa).ellipse((520, -420, 1500, 560), fill=tinta[:3])
    return Image.blend(base, capa.filter(ImageFilter.GaussianBlur(210)), fuerza)


def _partir(texto: str, fuente: ImageFont.FreeTypeFont, ancho: float) -> list[str]:
    lineas, actual = [], ""
    for palabra in texto.split(" "):          # solo espacios comunes: "USD 50.000" viaja pegado
        prueba = f"{actual} {palabra}".strip()
        if actual and fuente.getlength(prueba) > ancho:
            lineas.append(actual)
            actual = palabra
        else:
            actual = prueba
    if actual:
        lineas.append(actual)
    return lineas


_CIFRA = re.compile(r"\d")
_MONEDA = re.compile(r"(?i)^(usd|us\$|u\$s|\$|€|eur|ars|btc)$")
_MONEDA_Y_CIFRA = re.compile(r"(?i)(?<![\w$])(USD|US\$|U\$S|EUR|ARS|\$|€) (?=\d)")


def ajustar_titulo(texto: str, ancho: int, alto: int, tam_max: int, tam_min: int, max_lineas: int):
    """Busca el tamaño de letra más grande con el que el titular entra en su caja."""
    texto = re.sub(r"\s+", " ", texto).strip()
    # La moneda no se separa de su cifra: "USD 50.000" nunca queda partido en dos renglones.
    texto = _MONEDA_Y_CIFRA.sub(lambda m: m.group(1) + ESPACIO_DURO, texto)
    for tam in range(tam_max, tam_min - 1, -2):
        fuente = letra("titulo", tam)
        lineas = _partir(texto, fuente, ancho)
        entra = len(lineas) <= max_lineas and len(lineas) * round(tam * 1.1) <= alto
        if entra and all(fuente.getlength(l) <= ancho for l in lineas):
            # Evita que la última línea quede con una sola palabra suelta.
            for factor in (0.92, 0.84, 0.76):
                parejo = _partir(texto, fuente, ancho * factor)
                if len(parejo) == len(lineas) and len(lineas) > 1 and len(lineas[-1].split()) == 1:
                    lineas = parejo
                else:
                    break
            return fuente, lineas, round(tam * 1.1)
    fuente = letra("titulo", tam_min)
    lineas = _partir(texto, fuente, ancho)[:max_lineas]
    if lineas:
        while fuente.getlength(lineas[-1] + "…") > ancho and " " in lineas[-1]:
            lineas[-1] = lineas[-1].rsplit(" ", 1)[0]
        lineas[-1] += "…"
    return fuente, lineas, round(tam_min * 1.1)


def dibujar_titulo(d: ImageDraw.ImageDraw, x: int, y: int, lineas: list[str], fuente, interlineado: int, blanco, acento) -> int:
    """Escribe el titular; las cifras van en el color de acento. Devuelve la y final."""
    espacio = fuente.getlength(" ")
    for linea in lineas:
        palabras = linea.split(" ")
        cx = x
        for i, palabra in enumerate(palabras):
            siguiente = palabras[i + 1] if i + 1 < len(palabras) else ""
            resaltar = bool(_CIFRA.search(palabra)) or (bool(_MONEDA.match(palabra)) and bool(_CIFRA.search(siguiente)))
            visible = palabra.replace(ESPACIO_DURO, " ")
            d.text((cx, y), visible, font=fuente, fill=acento if resaltar else blanco)
            cx += fuente.getlength(visible) + espacio
        y += interlineado
    return y


def pastilla(d: ImageDraw.ImageDraw, x: int, y: int, texto: str, fondo, tinta=(8, 11, 15, 255)) -> tuple[int, int]:
    fuente = letra("negrita", 27)
    ancho = round(fuente.getlength(texto)) + 44
    alto = 52
    d.rounded_rectangle((x, y, x + ancho, y + alto), radius=10, fill=fondo)
    d.text((x + 22, y + alto / 2 + 1), texto, font=fuente, fill=tinta, anchor="lm")
    return ancho, alto


@lru_cache(maxsize=4)
def _logo_propio(tam: int) -> Image.Image | None:
    ruta = RAIZ / "assets" / "logo.png"
    if not ruta.exists():
        return None
    try:
        return Image.open(ruta).convert("RGBA").resize((tam, tam), Image.LANCZOS)
    except OSError:
        return None


def marca(base: Image.Image, d: ImageDraw.ImageDraw, cfg: dict, acento, y: int = 40, oscura: bool = False) -> None:
    """La firma arriba a la derecha: la cara de Eze en círculo y su nombre en dos renglones.
    ('NOMBRE | COMUNIDAD' en ajustes.yaml: lo de antes de la barra va arriba, lo de después abajo.)"""
    partes = [p.strip().upper() for p in str(cfg["marca"].get("nombre", "")).split("|") if p.strip()]
    derecha = LADO - MARGEN
    logo = _logo_propio(88)
    if logo is not None:
        base.paste(logo, (derecha - 88, y), logo)
        derecha -= 88 + 16
    tinta = (15, 20, 28, 255) if oscura else (255, 255, 255, 250)
    suave = (15, 20, 28, 185) if oscura else (255, 255, 255, 200)
    if len(partes) >= 2:
        d.text((derecha, y + 31), partes[0], font=letra("fuerte", 27), fill=tinta, anchor="rm")
        d.text((derecha, y + 61), partes[1], font=letra("media", 17), fill=suave, anchor="rm")
    elif partes:
        if logo is None:
            ancho = letra("fuerte", 25).getlength(partes[0])
            d.rectangle((derecha - ancho - 26, y + 36, derecha - ancho - 12, y + 50), fill=acento)
        d.text((derecha, y + 44), partes[0], font=letra("fuerte", 25), fill=tinta, anchor="rm")


def pie(d: ImageDraw.ImageDraw, izquierda: str, cfg: dict, momento: datetime, chico: str) -> None:
    d.line((MARGEN, 958, LADO - MARGEN, 958), fill=(255, 255, 255, 64), width=2)
    d.text((MARGEN, 992), izquierda[:46], font=letra("media", 27), fill=(255, 255, 255, 235), anchor="lm")
    firma = str(cfg["marca"].get("firma", ""))
    derecha = f"{fecha_corta(momento, cfg)}  ·  {firma}" if firma else fecha_corta(momento, cfg)
    d.text((LADO - MARGEN, 992), derecha, font=letra("normal", 24), fill=(255, 255, 255, 190), anchor="rm")
    if chico:
        fuente = letra("normal", 17)
        while fuente.getlength(chico) > LADO - 2 * MARGEN and len(chico) > 10:
            chico = chico[:-2].rstrip() + "…"
        d.text((MARGEN, 1036), chico, font=fuente, fill=(255, 255, 255, 140), anchor="lm")


def _bloque_titulo(d, post, urgente, acento, x, y_arriba=None, y_abajo=None, centro=None, ancho=LADO - 2 * MARGEN, alto=260, tam_max=84, max_lineas=4):
    """Titular (y cartel de ÚLTIMA HORA si corresponde): alineado arriba, abajo o centrado entre dos alturas."""
    fuente, lineas, inter = ajustar_titulo(post["titular_imagen"], ancho, alto, tam_max, 46, max_lineas)
    alto_titulo = len(lineas) * inter
    extra = 50 if urgente else 0
    if centro is not None:
        y = centro[0] + max(0, (centro[1] - centro[0] - alto_titulo - extra) // 2)
    elif y_arriba is not None:
        y = y_arriba
    else:
        y = y_abajo - alto_titulo - extra
    if urgente:
        d.text((x, y + 16), "ÚLTIMA HORA", font=letra("fuerte", 30), fill=acento, anchor="lm")
        y += extra
    return dibujar_titulo(d, x, y, lineas, fuente, inter, (255, 255, 255, 255), acento)


def _logo_claro(img: Image.Image) -> bool:
    """True si el logo es casi blanco (necesita fondo oscuro para verse)."""
    datos = img.convert("RGBA").resize((64, 64)).tobytes()
    visibles = [(datos[i], datos[i + 1], datos[i + 2]) for i in range(0, len(datos), 4) if datos[i + 3] > 60]
    if not visibles:
        return False
    brillo = sum(0.299 * r + 0.587 * g + 0.114 * b for r, g, b in visibles) / len(visibles)
    return brillo > 200


def armar_placa(post: dict, foto: Foto | None, cfg: dict, momento: datetime, variante: int = 0, urgente: bool = False) -> bytes:
    """Devuelve la imagen 1080x1080 de una noticia, en JPG."""
    colores = cfg.get("colores", {})
    acento = color(colores.get(post.get("tono", "neutral"), "#3B82F6"))
    fondo = color(colores.get("fondo", "#0B0F14"))
    base = Image.new("RGB", (LADO, LADO), fondo[:3])
    credito = foto.credito() if foto else ""
    categoria = str(post.get("categoria", "")).upper()
    corte = 600                                   # hasta dónde llega la foto en el diseño de panel

    vertical = bool(foto and foto.imagen is not None and foto.imagen.height > foto.imagen.width * 1.1)
    if foto and foto.imagen is not None and not foto.es_logo and (variante % 2 == 0 or vertical):
        # --- Diseño A: foto a pantalla completa, texto abajo (siempre que la foto sea vertical)
        base.paste(cubrir(foto.imagen, LADO, LADO), (0, 0))
        oscurecer(base, 0, 250, 165, hacia_abajo=False)
        oscurecer(base, 300, 800, 238)
        d = ImageDraw.Draw(base, "RGBA")
        pastilla(d, MARGEN, 56, categoria, acento)
        marca(base, d, cfg, acento)
        fin = 918
        fuente, lineas, inter = ajustar_titulo(post["titular_imagen"], LADO - 2 * MARGEN, 330, 94, 46, 4)
        inicio = fin - len(lineas) * inter - (50 if urgente else 0)
        d.rectangle((MARGEN, inicio - 30, MARGEN + 96, inicio - 22), fill=acento)
        _bloque_titulo(d, post, urgente, acento, MARGEN, y_abajo=fin, alto=330, tam_max=94)
    elif foto and foto.imagen is not None:
        # --- Diseño B: foto (o logo) arriba y panel oscuro con el texto abajo
        if foto.es_logo:
            claro = _logo_claro(foto.imagen)
            panel = (18, 24, 32) if claro else (244, 246, 248)
            base.paste(Image.new("RGB", (LADO, corte), panel), (0, 0))
            logo = foto.imagen.copy()
            logo.thumbnail((720, 380), Image.LANCZOS)
            base.paste(logo, ((LADO - logo.width) // 2, 120 + (corte - 120 - logo.height) // 2), logo)
            d = ImageDraw.Draw(base, "RGBA")
            marca(base, d, cfg, acento, oscura=not claro)
        else:
            base.paste(cubrir(foto.imagen, LADO, corte), (0, 0))
            oscurecer(base, 0, 240, 160, hacia_abajo=False)
            base.paste(Image.new("RGB", (LADO, LADO - corte), fondo[:3]), (0, corte))
            d = ImageDraw.Draw(base, "RGBA")
            marca(base, d, cfg, acento)
        d.rectangle((0, corte, LADO, corte + 8), fill=acento)
        pastilla(d, MARGEN, corte - 22, categoria, acento)
        _bloque_titulo(d, post, urgente, acento, MARGEN, centro=(corte + 52, 940), alto=280 - (50 if urgente else 0), tam_max=90)
    else:
        # --- Diseño C: solo texto (cuando no hay imagen real que tenga relación)
        base = _brillo(base, acento)
        d = ImageDraw.Draw(base, "RGBA")
        pastilla(d, MARGEN, 56, categoria, acento)
        marca(base, d, cfg, acento)
        fuente, lineas, inter = ajustar_titulo(post["titular_imagen"], LADO - 2 * MARGEN - 34, 560, 112, 46, 6)
        alto = len(lineas) * inter + (50 if urgente else 0)
        arriba = 250 + max(0, (640 - alto) // 2)
        d.rectangle((MARGEN, arriba + 6, MARGEN + 10, arriba + alto - 8), fill=acento)
        _bloque_titulo(d, post, urgente, acento, MARGEN + 34, y_arriba=arriba, ancho=LADO - 2 * MARGEN - 34, alto=560, tam_max=112, max_lineas=6)

    pie(d, f"Fuente: {post['fuente']}", cfg, momento, credito)
    return _a_jpg(base)


def tal_cual(imagen: Image.Image, lado_maximo: int = 1280) -> bytes:
    """Una imagen propia (por ejemplo, hecha a mano en ChatGPT) lista para publicar sin tocarla:
    solo se achica si es enorme y se pasa a JPG."""
    copia = imagen.convert("RGB")
    copia.thumbnail((lado_maximo, lado_maximo), Image.LANCZOS)
    return _a_jpg(copia)


def _a_jpg(base: Image.Image) -> bytes:
    salida = io.BytesIO()
    base.convert("RGB").save(salida, "JPEG", quality=90, optimize=True, subsampling=0)
    return salida.getvalue()


def _tilde(d: ImageDraw.ImageDraw, x: int, y: int, tinta) -> None:
    """Un cuadradito con un tilde, para las listas de las promos."""
    d.rounded_rectangle((x, y, x + 40, y + 40), radius=10, fill=tinta)
    d.line((x + 10, y + 21, x + 17, y + 28, x + 30, y + 13), fill=(8, 11, 15, 255), width=5, joint="curve")


def armar_promo(promo: dict, etiqueta: str, boton: str, aviso: str, cfg: dict, momento: datetime,
                imagen: Image.Image | None = None, logo: Image.Image | None = None) -> bytes:
    """La placa de una venta: etiqueta, gancho, hasta 3 puntos y un botón con el llamado.
    Abajo dice que es contenido promocional y, si corresponde, el aviso de riesgo.
    'imagen' es una foto propia (va arriba); 'logo' es el logo oficial de la marca (va en una ficha)."""
    colores = cfg.get("colores", {})
    acento = color(colores.get("promo", "#F5B50A"))
    fondo = color(colores.get("fondo", "#0B0F14"))
    base = Image.new("RGB", (LADO, LADO), fondo[:3])
    if imagen is not None:
        alto_foto = 400
        base.paste(cubrir(imagen.convert("RGB"), LADO, alto_foto), (0, 0))
        oscurecer(base, 0, 240, 160, hacia_abajo=False)
        base.paste(Image.new("RGB", (LADO, LADO - alto_foto), fondo[:3]), (0, alto_foto))
        d = ImageDraw.Draw(base, "RGBA")
        d.rectangle((0, alto_foto, LADO, alto_foto + 8), fill=acento)
        pastilla(d, MARGEN, alto_foto - 22, etiqueta.upper(), acento)
        arriba, tam_max = alto_foto + 50, 72
    else:
        base = _brillo(base, acento, 0.5)
        d = ImageDraw.Draw(base, "RGBA")
        pastilla(d, MARGEN, 56, etiqueta.upper(), acento)
        arriba, tam_max = 180, 92
        if logo is not None:
            # Ficha con el logo oficial de la marca, arriba del gancho.
            sello = logo.convert("RGBA")
            caja = sello.getchannel("A").getbbox()        # sin los bordes vacíos del archivo
            if caja:
                sello = sello.crop(caja)
            sello.thumbnail((300, 84), Image.LANCZOS)
            claro = _logo_claro(sello)
            ancho_ficha, alto_ficha = sello.width + 48, 128
            d.rounded_rectangle((MARGEN, 172, MARGEN + ancho_ficha, 172 + alto_ficha), radius=22,
                                fill=(18, 24, 32, 255) if claro else (246, 247, 249, 255),
                                outline=(255, 255, 255, 60) if claro else None, width=2)
            base.paste(sello, (MARGEN + 24, 172 + (alto_ficha - sello.height) // 2), sello)
            arriba = 172 + alto_ficha + 36
    marca(base, d, cfg, acento)

    abajo = 792                                    # el botón empieza en 818
    puntos = list(promo.get("puntos") or [])[:3]
    alto_puntos = len(puntos) * 62 + (34 if puntos else 0)
    fuente, lineas, inter = ajustar_titulo(promo["titular_imagen"], LADO - 2 * MARGEN, abajo - arriba - alto_puntos, tam_max, 42, 4)
    y = arriba + max(0, (abajo - arriba - len(lineas) * inter - alto_puntos) // 2)
    y = dibujar_titulo(d, MARGEN, y, lineas, fuente, inter, (255, 255, 255, 255), acento) + 34
    ancho_punto = LADO - 2 * MARGEN - 58
    for punto in puntos:
        _tilde(d, MARGEN, y + 6, acento)
        tam = 35                                   # si el renglón es largo se achica la letra; si aun así no entra, se corta
        while tam > 27 and letra("media", tam).getlength(punto) > ancho_punto:
            tam -= 2
        while letra("media", tam).getlength(punto) > ancho_punto and len(punto) > 10:
            punto = punto[:-2].rstrip() + "…"
        d.text((MARGEN + 58, y + 27), punto, font=letra("media", tam), fill=(255, 255, 255, 240), anchor="lm")
        y += 62

    d.rounded_rectangle((MARGEN, 818, LADO - MARGEN, 904), radius=18, fill=acento)
    texto_boton = boton.upper()
    tam = 36
    while tam > 22 and letra("fuerte", tam).getlength(texto_boton) > LADO - 2 * MARGEN - 56:
        tam -= 2
    d.text((LADO / 2, 862), texto_boton, font=letra("fuerte", tam), fill=(8, 11, 15, 255), anchor="mm")

    pie(d, "Contenido promocional", cfg, momento, aviso)
    return _a_jpg(base)
