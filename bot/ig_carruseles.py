"""Los 4 estilos de carrusel de Instagram, sacados de los carruseles con más interacción del nicho (oct. 2026).

Formato 3:4 (1080x1440), el que Instagram muestra entero en la grilla del perfil.

  titular   -> como @seb.ai ("I built an AI that finds crypto before it pumps", 3.300 comentarios):
               fondo negro, letra gigante angosta en mayúsculas, UNA palabra en verde, captura o gráfico
               abajo, contador 1/8. Cierre "Comentá PALABRA" (el que más comentarios junta).
  editorial -> como @cursofinanzas_ y @theunitedcredit (3.000 a 5.000 me gusta): titular enorme con
               serif, la frase clave sobre una banda amarilla, foto real abajo, lista con casillas.
  partida   -> como @edugomez0304 y @santiamadofx (2.000+ en trading): la lámina partida en dos fotos,
               "El mundo lo llama ___ / Nosotros lo llamamos ___", una palabra en naranja.
  noticia   -> como @bitcoin_argentina: foto arriba, etiqueta de categoría, titular en negrita,
               fecha y fuente. Para el resumen semanal de @comunidad_inversores.

En los textos, la palabra entre *asteriscos* es la que se resalta (color o banda, según el estilo)."""
from __future__ import annotations

import io
import random
import re

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .imagen import LETRAS, _logo_propio, color, cubrir, letra, oscurecer

ANCHO, ALTO = 1080, 1440
M = 80                                   # margen

NEGRO = (10, 10, 10, 255)
BLANCO = (255, 255, 255, 255)
GRIS = (160, 160, 160, 255)
VERDE = color("#22C55E")
DORADO = color("#F5B50A")
NARANJA = color("#F59E0B")
AMARILLO = color("#FFD60A")
PAPEL = (244, 241, 234, 255)
TINTA = (17, 17, 17, 255)


def fuente(nombre: str, tam: int) -> ImageFont.FreeTypeFont:
    archivos = {"angosta": "Anton-Regular.ttf", "serif": "DMSerifDisplay-Regular.ttf"}
    if nombre in archivos:
        return ImageFont.truetype(str(LETRAS / archivos[nombre]), tam)
    return letra(nombre, tam)


def _jpg(img: Image.Image) -> bytes:
    b = io.BytesIO()
    img.convert("RGB").save(b, "JPEG", quality=92, optimize=True)
    return b.getvalue()


# ------------------------------------------------------------- texto con palabra resaltada

def _palabras(texto: str) -> list[tuple[str, bool]]:
    """'el *bot* gana' -> [('el', False), ('bot', True), ('gana', False)]. Sirve para varias palabras: '*sin riesgo*'."""
    salida, marcado = [], False
    for trozo in re.split(r"(\*)", texto):
        if trozo == "*":
            marcado = not marcado
            continue
        for p in trozo.split():
            # Un signo suelto (".", "?") va pegado a la palabra anterior: "*stop loss*." no deja el punto colgado.
            if salida and re.fullmatch(r"[.,;:!?)»…]+", p):
                salida[-1] = (salida[-1][0] + p, salida[-1][1])
            else:
                salida.append((p, marcado))
    return salida


def _renglones(texto: str, f: ImageFont.FreeTypeFont, ancho: int) -> list[list[tuple[str, bool]]]:
    renglones = []
    for parrafo in texto.split("\n"):
        actual: list[tuple[str, bool]] = []
        for p in _palabras(parrafo):
            prueba = " ".join(x for x, _ in actual + [p])
            if actual and f.getlength(prueba) > ancho:
                renglones.append(actual)
                actual = [p]
            else:
                actual.append(p)
        if actual:
            renglones.append(actual)
    return renglones


def _que_entre(texto: str, nombre: str, ancho: int, alto: int, tam_max: int, tam_min: int, inter: float) -> int:
    for tam in range(tam_max, tam_min - 1, -4):
        f = fuente(nombre, tam)
        if len(_renglones(texto, f, ancho)) * tam * inter <= alto:
            return tam
    return tam_min


def escribir(d: ImageDraw.ImageDraw, x: int, y: int, texto: str, nombre: str, tam: int, ancho: int, tinta, acento,
             modo: str = "color", centrado: bool = False, inter: float = 1.1, tinta_banda=TINTA) -> int:
    """Escribe el texto partiéndolo en renglones; las palabras *marcadas* van en color ('color') o sobre una
    banda ('banda'). Devuelve la y donde terminó."""
    f = fuente(nombre, tam)
    espacio = f.getlength(" ")
    for renglon in _renglones(texto, f, ancho):
        largo = sum(f.getlength(p) for p, _ in renglon) + espacio * (len(renglon) - 1)
        cx = x + (ancho - largo) / 2 if centrado else x
        if modo == "banda":
            # La banda abarca todas las palabras marcadas seguidas del renglón.
            i = 0
            while i < len(renglon):
                if renglon[i][1]:
                    j, x0 = i, cx + sum(f.getlength(p) + espacio for p, _ in renglon[:i])
                    while j < len(renglon) and renglon[j][1]:
                        j += 1
                    x1 = x0 + sum(f.getlength(p) for p, _ in renglon[i:j]) + espacio * (j - i - 1)
                    caja = d.textbbox((0, y), "Ág", font=f)
                    d.rectangle((x0 - 12, caja[1] - 4, x1 + 12, caja[3] + 6), fill=acento)
                    i = j
                else:
                    i += 1
        for p, marcada in renglon:
            if modo == "banda":
                t = tinta_banda if marcada else tinta
            else:
                t = acento if marcada else tinta
            d.text((cx, y), p, font=f, fill=t)
            cx += f.getlength(p) + espacio
        y += round(tam * inter)
    return y


def alto_de(texto: str, nombre: str, tam: int, ancho: int, inter: float = 1.1) -> int:
    return len(_renglones(texto, fuente(nombre, tam), ancho)) * round(tam * inter)


# ------------------------------------------------------------- piezas comunes

def _contador(d: ImageDraw.ImageDraw, i: int, total: int, tinta=GRIS) -> None:
    d.text((ANCHO - M, ALTO - 70), f"{i}/{total}", font=fuente("media", 30), fill=tinta, anchor="rm")


def _usuario(base: Image.Image, d: ImageDraw.ImageDraw, usuario: str, tinta=GRIS, y: int | None = None) -> None:
    y = ALTO - 70 if y is None else y
    logo = _logo_propio(52)
    x = M
    if logo:
        mascara = Image.new("L", logo.size, 0)
        ImageDraw.Draw(mascara).ellipse((0, 0, *logo.size), fill=255)
        base.paste(logo, (x, y - 26), mascara)
        x += 66
    d.text((x, y), usuario, font=fuente("media", 30), fill=tinta, anchor="lm")


def _caja_redondeada(base: Image.Image, img: Image.Image, caja: tuple[int, int, int, int], radio: int = 28,
                     borde=(255, 255, 255, 40)) -> None:
    x0, y0, x1, y1 = caja
    foto = cubrir(img.convert("RGB"), x1 - x0, y1 - y0)
    mascara = Image.new("L", foto.size, 0)
    ImageDraw.Draw(mascara).rounded_rectangle((0, 0, *foto.size), radio, fill=255)
    base.paste(foto, (x0, y0), mascara)
    ImageDraw.Draw(base, "RGBA").rounded_rectangle(caja, radio, outline=borde, width=2)


def _logo_con_brillo(base: Image.Image, logo: Image.Image, caja: tuple[int, int, int, int]) -> None:
    """El logo oficial grande en el centro, con un brillo verde detrás."""
    x0, y0, x1, y1 = caja
    lado = min(x1 - x0, y1 - y0, 560)
    cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
    brillo = Image.new("RGBA", base.size, (0, 0, 0, 0))
    ImageDraw.Draw(brillo).ellipse((cx - lado * 0.7, cy - lado * 0.7, cx + lado * 0.7, cy + lado * 0.7), fill=(34, 197, 94, 70))
    base.alpha_composite(brillo.filter(ImageFilter.GaussianBlur(90)))
    l = logo.convert("RGBA")
    escala = lado * 0.8 / max(l.size)
    l = l.resize((max(1, round(l.width * escala)), max(1, round(l.height * escala))), Image.LANCZOS)
    base.alpha_composite(l, (cx - l.width // 2, cy - l.height // 2))


def grafico_velas(ancho: int, alto: int, semilla: int = 7, sube: bool = True) -> Image.Image:
    """Un gráfico de velas de ejemplo (sin datos reales) para cuando no hay captura."""
    rnd = random.Random(semilla)
    img = Image.new("RGB", (ancho, alto), (14, 17, 22))
    d = ImageDraw.Draw(img)
    for yy in range(0, alto, 60):
        d.line((0, yy, ancho, yy), fill=(28, 32, 38))
    n, precio, velas = 42, 100.0, []
    for k in range(n):
        tendencia = (0.9 if sube else -0.9) * (1.8 if k > n * 0.6 else 0.4)
        apertura = precio
        precio = max(5.0, precio + rnd.gauss(tendencia, 2.2))
        velas.append((apertura, precio, max(apertura, precio) + abs(rnd.gauss(0, 1.4)), min(apertura, precio) - abs(rnd.gauss(0, 1.4))))
    alto_max, bajo_min = max(v[2] for v in velas), min(v[3] for v in velas)
    escala = lambda v: alto - 30 - (v - bajo_min) / (alto_max - bajo_min) * (alto - 60)
    paso = ancho / n
    for k, (a, c, h, l) in enumerate(velas):
        cx = paso * k + paso / 2
        tono = (34, 197, 94) if c >= a else (239, 68, 68)
        d.line((cx, escala(h), cx, escala(l)), fill=tono, width=2)
        d.rectangle((cx - paso * 0.3, min(escala(a), escala(c)), cx + paso * 0.3, max(escala(a), escala(c)) + 1), fill=tono)
    return img


# ============================================================= 1) TITULAR (estilo @seb.ai)

def titular(laminas: list[dict], usuario: str = "@ezebagnato") -> list[bytes]:
    """laminas: [{'tipo': 'portada', 'titulo': 'ARMÉ UN *BOT* QUE...', 'bajada': '...', 'imagen': PIL|None},
                 {'tipo': 'paso', 'titulo': '...', 'texto': '...', 'imagen': PIL|None},
                 {'tipo': 'cierre', 'palabra': 'BOT', 'texto': 'y te mando ... por privado'}]"""
    total, salida = len(laminas), []
    for i, l in enumerate(laminas, 1):
        base = Image.new("RGBA", (ANCHO, ALTO), NEGRO)
        d = ImageDraw.Draw(base)
        tipo = l.get("tipo", "paso")
        if tipo == "cierre":
            y = 360
            d.text((ANCHO // 2, y), "COMENTÁ", font=fuente("angosta", 120), fill=BLANCO, anchor="mt")
            palabra = str(l.get("palabra", "BOT")).upper()
            f = fuente("angosta", 260)
            ancho_p = f.getlength(palabra)
            d.rounded_rectangle(((ANCHO - ancho_p) / 2 - 40, y + 170, (ANCHO + ancho_p) / 2 + 40, y + 500), 30, fill=VERDE)
            d.text((ANCHO // 2, y + 335), palabra, font=f, fill=NEGRO, anchor="mm")
            escribir(d, M + 40, y + 580, l.get("texto", "y te mando el link por privado"), "media", 46,
                     ANCHO - 2 * M - 80, BLANCO, VERDE, centrado=True, inter=1.3)
            if l.get("extra"):
                escribir(d, M + 40, ALTO - 260, l["extra"], "normal", 34, ANCHO - 2 * M - 80, GRIS, VERDE, centrado=True)
        elif tipo == "paso" and l.get("imagen") is None:
            # Sin imagen que tenga que ver con el tema, la lámina va solo con texto grande (nada de gráficos de relleno).
            titulo = str(l.get("titulo", "")).upper()
            ancho = ANCHO - 2 * M
            tam_t = _que_entre(titulo, "angosta", ancho, 330, 132, 70, 1.12)
            alto_total = alto_de(titulo, "angosta", tam_t, ancho, 1.12) + 60 + alto_de(l.get("texto", ""), "media", 56, ancho, 1.35)
            y = max(140, (ALTO - 120 - alto_total) // 2)
            d.rectangle((M, y - 50, M + 120, y - 38), fill=VERDE)
            y = escribir(d, M, y, titulo, "angosta", tam_t, ancho, BLANCO, VERDE, inter=1.12) + 60
            escribir(d, M, y, l.get("texto", ""), "media", 56, ancho, (225, 225, 225, 255), VERDE, inter=1.35)
        else:
            portada = tipo == "portada"
            titulo = str(l.get("titulo", "")).upper()
            alto_caja = 430 if portada else 300
            tam = _que_entre(titulo, "angosta", ANCHO - 2 * M, alto_caja, 150 if portada else 104, 60, 1.16)
            y = escribir(d, M, 90, titulo, "angosta", tam, ANCHO - 2 * M, BLANCO, VERDE, centrado=portada, inter=1.16)
            if l.get("bajada"):
                y = escribir(d, M, y + 20, str(l["bajada"]).upper(), "fuerte", 34, ANCHO - 2 * M, GRIS, VERDE,
                             centrado=portada) + 10
            if l.get("texto"):
                y = escribir(d, M, y + 40, l["texto"], "normal", 42, ANCHO - 2 * M, (215, 215, 215, 255), VERDE,
                             inter=1.35) + 10
            caja = (M, max(y + 40, 560 if portada else y + 40), ANCHO - M, ALTO - 130)
            if l.get("logo") is not None:
                _logo_con_brillo(base, l["logo"], caja)
            elif caja[3] - caja[1] > 200:
                _caja_redondeada(base, l.get("imagen") or grafico_velas(ANCHO - 2 * M, 600, semilla=i), caja)
        _usuario(base, d, usuario)
        _contador(d, i, total)
        if i == 1:
            d.text((ANCHO - M - 90, ALTO - 70), "DESLIZÁ  →", font=fuente("negrita", 28), fill=VERDE, anchor="rm")
        salida.append(_jpg(base))
    return salida


# ============================================================= 2) EDITORIAL (estilo @cursofinanzas_)

def _casilla(d: ImageDraw.ImageDraw, x: int, y: int, tam: int = 34, tinta=TINTA) -> None:
    d.rectangle((x, y, x + tam, y + tam), outline=tinta, width=4)


def editorial(laminas: list[dict], usuario: str = "@ezebagnato") -> list[bytes]:
    """laminas: [{'tipo': 'portada', 'numero': '7', 'titulo': 'HÁBITOS QUE...', 'banda': 'TE HACEN *PERDER DINERO*', 'foto': PIL|None},
                 {'tipo': 'punto', 'titulo': 'PREGUNTÁ EL PRECIO.', 'lista': ['...', '...'], 'banda': '*frase clave*', 'foto': PIL|None},
                 {'tipo': 'cierre', 'titulo': '...', 'texto': 'Guardalo y empezá hoy'}]"""
    total, salida = len(laminas), []
    for i, l in enumerate(laminas, 1):
        base = Image.new("RGBA", (ANCHO, ALTO), PAPEL)
        foto = l.get("foto")
        alto_foto = 560 if foto is not None else 0
        if foto is not None:
            base.paste(cubrir(foto.convert("RGB"), ANCHO, alto_foto), (0, ALTO - alto_foto))
            sombra = Image.new("RGBA", (ANCHO, 140), (0, 0, 0, 0))
            for yy in range(140):
                ImageDraw.Draw(sombra).line((0, yy, ANCHO, yy), fill=(*PAPEL[:3], round(255 * (1 - yy / 140))))
            base.alpha_composite(sombra, (0, ALTO - alto_foto))
        capa = Image.new("RGBA", base.size, (0, 0, 0, 0))
        d = ImageDraw.Draw(capa)
        tipo = l.get("tipo", "punto")
        y = 110
        if tipo == "portada" and l.get("numero"):
            f = fuente("serif", 330)
            d.text((M - 10, y - 70), str(l["numero"]), font=f, fill=TINTA)
            x_t = M + f.getlength(str(l["numero"])) + 20
            tam = _que_entre(str(l.get("titulo", "")).upper(), "serif", ANCHO - x_t - M, 300, 110, 56, 1.0)
            escribir(d, x_t, y, str(l.get("titulo", "")).upper(), "serif", tam, ANCHO - x_t - M, TINTA, TINTA, inter=1.0)
            y += 330
        else:
            titulo = str(l.get("titulo", "")).upper()
            tam = _que_entre(titulo, "serif", ANCHO - 2 * M, 320, 150 if tipo != "portada" else 170, 70, 0.98)
            y = escribir(d, M, y, titulo, "serif", tam, ANCHO - 2 * M, TINTA, AMARILLO, modo="banda", inter=1.08) + 50
        for item in l.get("lista") or []:
            _casilla(d, M, y + 8, 40)
            y = escribir(d, M + 70, y, item, "media", 46, ANCHO - 2 * M - 70, TINTA, TINTA, inter=1.3) + 14
        if l.get("banda"):
            y += 30
            y = escribir(d, M, y, l["banda"], "fuerte" if tipo != "portada" else "serif", 62 if tipo != "portada" else 96,
                         ANCHO - 2 * M, TINTA, AMARILLO, modo="banda", inter=1.3)
        if l.get("texto"):
            escribir(d, M, y + 40, l["texto"], "normal", 46, ANCHO - 2 * M, (60, 60, 60, 255), AMARILLO, modo="banda", inter=1.4)
        caja = capa.getbbox()
        if caja and foto is None:
            # Sin foto, el bloque de texto va centrado en la lámina (como en los originales).
            arriba = max(90, (ALTO - 140 - (caja[3] - caja[1])) // 2) - caja[1]
            base.alpha_composite(capa, (0, arriba))
        else:
            base.alpha_composite(capa)
        d = ImageDraw.Draw(base)
        tinta_pie = BLANCO if foto is not None else (90, 90, 90, 255)
        _usuario(base, d, usuario, tinta=tinta_pie)
        _contador(d, i, total, tinta=tinta_pie)
        salida.append(_jpg(base))
    return salida


# ============================================================= 3) PARTIDA (estilo @edugomez0304)

def _mitad(base: Image.Image, foto: Image.Image | None, y0: int, alto: int, texto: str, acento) -> None:
    if foto is None:
        foto = grafico_velas(ANCHO, alto, semilla=y0 + 3)
    trozo = cubrir(foto.convert("RGB"), ANCHO, alto).filter(ImageFilter.GaussianBlur(0.5))
    capa = Image.new("RGBA", trozo.size, (0, 0, 0, 95))
    trozo = Image.alpha_composite(trozo.convert("RGBA"), capa)
    base.paste(trozo, (0, y0))
    d = ImageDraw.Draw(base)
    tam = _que_entre(texto, "fuerte", ANCHO - 2 * M, 220, 64, 40, 1.15)
    alto_t = alto_de(texto, "fuerte", tam, ANCHO - 2 * M, 1.15)
    ty = y0 + (alto - alto_t) // 2
    # Sombra suave para que se lea sobre cualquier foto.
    sombra = Image.new("RGBA", base.size, (0, 0, 0, 0))
    escribir(ImageDraw.Draw(sombra), M + 3, ty + 4, texto, "fuerte", tam, ANCHO - 2 * M, (0, 0, 0, 200), (0, 0, 0, 200),
             centrado=True, inter=1.15)
    base.alpha_composite(sombra.filter(ImageFilter.GaussianBlur(6)))
    escribir(d, M, ty, texto, "fuerte", tam, ANCHO - 2 * M, BLANCO, acento, centrado=True, inter=1.15)


def partida(laminas: list[dict], usuario: str = "@ezebagnato") -> list[bytes]:
    """laminas: [{'arriba': 'El mundo lo llama *apostar*', 'foto_arriba': PIL|None,
                  'abajo': 'Nosotros lo llamamos *trading*', 'foto_abajo': PIL|None}, ...,
                 {'tipo': 'cierre', 'texto': '...', 'palabra': 'PLAN'}]"""
    total, salida = len(laminas), []
    for i, l in enumerate(laminas, 1):
        base = Image.new("RGBA", (ANCHO, ALTO), NEGRO)
        if l.get("tipo") == "cierre":
            d = ImageDraw.Draw(base)
            y = escribir(d, M, 420, l.get("texto", ""), "fuerte", 70, ANCHO - 2 * M, BLANCO, NARANJA, centrado=True, inter=1.2)
            if l.get("palabra"):
                escribir(d, M, y + 80, f"Comentá *{l['palabra'].upper()}* y te mando la guía", "media", 48,
                         ANCHO - 2 * M, BLANCO, NARANJA, centrado=True, inter=1.3)
        else:
            mitad = ALTO // 2
            _mitad(base, l.get("foto_arriba"), 0, mitad, l.get("arriba", ""), NARANJA)
            _mitad(base, l.get("foto_abajo"), mitad, ALTO - mitad, l.get("abajo", ""), NARANJA)
            d = ImageDraw.Draw(base)
            d.line((0, mitad, ANCHO, mitad), fill=(0, 0, 0, 255), width=6)
        d = ImageDraw.Draw(base)
        _usuario(base, d, usuario, tinta=BLANCO)
        _contador(d, i, total, tinta=BLANCO)
        salida.append(_jpg(base))
    return salida


# ============================================================= 4) NOTICIA (estilo @bitcoin_argentina)

def noticia(laminas: list[dict], usuario: str = "@comunidad_inversores_ok") -> list[bytes]:
    """laminas: [{'foto': PIL|None, 'categoria': 'INSTITUCIONES · EE.UU.', 'titular': 'JPMorgan ...',
                  'fecha': '20 oct 2026', 'fuente': 'Reuters'}, ...]"""
    total, salida = len(laminas), []
    for i, l in enumerate(laminas, 1):
        base = Image.new("RGBA", (ANCHO, ALTO), NEGRO)
        alto_foto = 860
        foto = l.get("foto") or grafico_velas(ANCHO, alto_foto, semilla=i)
        base.paste(cubrir(foto.convert("RGB"), ANCHO, alto_foto), (0, 0))
        oscurecer(base, alto_foto - 420, alto_foto + 2, 255)
        d = ImageDraw.Draw(base)
        # Arriba: la marca de la cuenta, como un medio.
        _usuario(base, d, usuario, tinta=BLANCO, y=80)
        titular_txt = str(l.get("titular", ""))
        tam = _que_entre(titular_txt, "titulo", ANCHO - 2 * M, 400, 84, 52, 1.1)
        alto_t = alto_de(titular_txt, "titulo", tam, ANCHO - 2 * M, 1.1)
        y = ALTO - 160 - alto_t
        cat = str(l.get("categoria", "")).upper()
        if cat:
            f = fuente("negrita", 28)
            d.rounded_rectangle((M, y - 80, M + f.getlength(cat) + 44, y - 26), 27, fill=DORADO)
            d.text((M + 22, y - 53), cat, font=f, fill=NEGRO, anchor="lm")
        escribir(d, M, y, titular_txt, "titulo", tam, ANCHO - 2 * M, BLANCO, DORADO, inter=1.1)
        pie = "  ·  ".join(x for x in (l.get("fecha"), f"Fuente: {l['fuente']}" if l.get("fuente") else "") if x)
        d.text((M, ALTO - 70), pie, font=fuente("media", 28), fill=GRIS, anchor="lm")
        _contador(d, i, total)
        salida.append(_jpg(base))
    return salida
