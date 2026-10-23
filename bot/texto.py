"""Arma los textos finales y los controla:
- que no se cuele ningún link que no sea el tuyo,
- que no aparezca ningún número que no esté en la noticia original (o en los datos de la promo),
- que una promo nunca prometa ganancias,
- que el texto entre en X y en Telegram."""
from __future__ import annotations

import html
import re
import unicodedata
from datetime import datetime
from decimal import Decimal, InvalidOperation

from .filtro import fichas
from .util import ESPACIO_INVISIBLE, normalizar, sin_acentos

LIMITE_X = 275          # X permite 280; se deja un margen
LIMITE_TELEGRAM = 1000  # el pie de foto de Telegram permite 1024
LARGO_LINK_X = 23       # para X cualquier link cuenta 23 caracteres

EMOJI_CATEGORIA = {
    "ECONOMÍA": "📊",
    "MERCADOS": "📈",
    "INDICADORES": "🧮",
    "CRIPTO": "🪙",
    "EXCHANGES": "💱",
    "BROKERS": "🏛️",
    "EMPRESAS": "🏢",
    "GEOPOLÍTICA": "🌍",
    "REGULACIÓN": "⚖️",
    "ARGENTINA": "🇦🇷",
}

# ------------------------------------------------------------------ links

_URL = re.compile(r"(?i)\b(?:https?://|www\.|t\.me/)\S+")
# 'subió.Ahora' (falta un espacio después del punto) -> 'subió. Ahora'
_PEGADO = re.compile(r"([a-záéíóúñü])\.([A-ZÁÉÍÓÚÑ][a-záéíóúñü])")
# Cualquier 'palabra.palabra' puede terminar convertida en link por X o Telegram (hay más de mil
# terminaciones válidas: .com, .news, .today, .fun...). No alcanza con una lista: se cortan todas.
_PUNTO = re.compile(r"([^\W_]+)\.(?=([^\W\d_]{2,}))")
_CASHTAG = re.compile(r"(?<![\w$])\$(?=[A-Za-z]{1,6}\b)")


def _es_sigla(izquierda: str, derecha: str) -> bool:
    """EE.UU., FF.AA., RR.HH.: siglas en plural, que no son un link."""
    def doble(s: str) -> bool:
        return len(s) == 2 and s.isupper() and s[0] == s[1]
    return doble(izquierda) and doble(derecha)


def _romper_punto(m: re.Match) -> str:
    if _es_sigla(m.group(1), m.group(2)):
        return m.group(0)
    # 'Crypto.com' se sigue leyendo igual, pero con un espacio invisible deja de ser un link.
    return m.group(1) + "." + ESPACIO_INVISIBLE


def sin_links(texto: str) -> str:
    """Deja el texto sin nada que X o Telegram puedan convertir en link, mención, hashtag o cashtag.
    (Se usa sobre lo que escribe la IA y sobre los nombres de los medios; tus links los agrega el
    programa después.)"""
    t = _URL.sub("", texto or "")
    t = _PEGADO.sub(r"\1. \2", t)
    t = _PUNTO.sub(_romper_punto, t)
    t = re.sub(r"@(?=\w)", "", t)
    t = re.sub(r"#(?=\w)", "", t)
    t = _CASHTAG.sub("", t)
    t = re.sub(r"[ \t]+", " ", t)
    return re.sub(r" ?\n ?", "\n", t).strip()


def parece_link(texto: str) -> bool:
    if _URL.search(texto):
        return True
    return any(not _es_sigla(m.group(1), m.group(2)) for m in _PUNTO.finditer(texto))


# ------------------------------------------------------------ largo en X

def peso_x(texto: str) -> int:
    """Cuánto 'pesa' un texto para X (los emojis valen 2 y cualquier link vale 23)."""
    sin_urls, cantidad = re.subn(r"https?://\S+", "", texto)
    total = cantidad * LARGO_LINK_X
    for c in unicodedata.normalize("NFC", sin_urls):
        o = ord(c)
        if o == 0xFE0F or o == 0x200D or 0x1F3FB <= o <= 0x1F3FF:
            continue                      # piezas invisibles de un emoji
        if 0x1F1E6 <= o <= 0x1F1FF:
            total += 1                    # una bandera son dos de estas = 2
        elif o <= 0x10FF or 0x2000 <= o <= 0x200D or 0x2010 <= o <= 0x201F or 0x2032 <= o <= 0x2037:
            total += 1
        else:
            total += 2
    return total


def recortar_x(texto: str, limite: int = LIMITE_X) -> str:
    if peso_x(texto) <= limite:
        return texto
    palabras = texto.split(" ")
    while palabras and peso_x(" ".join(palabras) + "…") > limite:
        palabras.pop()
    return " ".join(palabras).rstrip(" ,;:.-") + "…"


def _primera_que_entra(opciones: list[list[str]], limite: int = LIMITE_X) -> str | None:
    for partes in opciones:
        candidato = "\n\n".join(p for p in partes if p)
        if peso_x(candidato) <= limite:
            return candidato
    return None


# ------------------------------------------------------ control de cifras

_NUMERO = re.compile(r"\d+(?:[.,]\d+)*")
# La palabra que sigue a un número le cambia el tamaño: "83 mil", "1,2 mil millones", "$5M".
_ESCALAS = [
    (re.compile(r"(?i)\s*(?:mil\s+millones|miles\s+de\s+millones|billions?|bn)(?![\w])"), 9),
    (re.compile(r"(?i)\s*(?:billones|bill[oó]n|trillions?)(?![\w])"), 12),
    (re.compile(r"(?i)\s*(?:millones|mill[oó]n|millions?|mn|mm)(?![\w])"), 6),
    (re.compile(r"(?i)\s*(?:mil|miles|thousands?)(?![\w])"), 3),
    (re.compile(r"[kK](?![\w])"), 3),          # pegado al número: 83k
    (re.compile(r"M(?![\w])"), 6),             # 5M
    (re.compile(r"B(?![\w])"), 9),             # 1.2B
]


def _valores(numero: str) -> set[Decimal]:
    """Lo que puede valer un número escrito con puntos y comas.
    '6.432,10' -> 6432.10 · '1,5' -> 1.5 · '83.000' -> 83000 · '1.485' -> 1485 o 1,485 (se aceptan los dos)."""
    puntos, comas = numero.count("."), numero.count(",")
    opciones: list[str] = []
    if puntos and comas:
        decimal = "." if numero.rfind(".") > numero.rfind(",") else ","
        miles = "," if decimal == "." else "."
        entero, _, fraccion = numero.replace(miles, "").partition(decimal)
        opciones.append(f"{entero}.{fraccion}")
    elif not (puntos or comas):
        opciones.append(numero)
    else:
        partes = numero.split("." if puntos else ",")
        if len(partes) > 2:                            # 1.234.567: son separadores de miles
            opciones.append("".join(partes))
        else:
            entero, fraccion = partes
            if len(fraccion) == 3:
                opciones.append(entero + fraccion)     # 83.000 = ochenta y tres mil
            if fraccion != "000":
                opciones.append(f"{entero}.{fraccion}")    # 1,5 · 3.50 · y también 1.485 leído con decimales
    valores: set[Decimal] = set()
    for o in opciones:
        try:
            valores.add(Decimal(o))
        except InvalidOperation:
            pass
    return valores


def _cifras(texto: str) -> list[tuple[str, set[Decimal]]]:
    """Cada número del texto con lo que vale (teniendo en cuenta 'mil', 'millones', 'k', 'M'...)."""
    texto = texto or ""
    salida: list[tuple[str, set[Decimal]]] = []
    for m in _NUMERO.finditer(texto):
        valores = _valores(m.group(0))
        for patron, exponente in _ESCALAS:
            if patron.match(texto, m.end()):
                valores = {v * (Decimal(10) ** exponente) for v in valores}
                break
        salida.append((m.group(0), valores))
    return salida


def numeros(texto: str) -> set[Decimal]:
    """Todos los valores numéricos que aparecen en un texto."""
    todos: set[Decimal] = set()
    for _, valores in _cifras(texto):
        todos |= valores
    return todos


def numeros_inventados(salida: str, fuente: str, permitidos: set[str] | None = None) -> list[str]:
    """Números que aparecen en lo que escribió la IA pero NO en el texto de origen.

    Se compara por valor: '1,5' y '1.5' son lo mismo, '83 mil' y '83,000' también, y '1.200 millones'
    es igual a '1.2 billion'. En cambio '15' no es '1.5', ni '350' es '3.50'."""
    reales = numeros(fuente)
    for p in permitidos or set():
        reales |= _valores(str(p))
    return sorted({numero for numero, valores in _cifras(salida) if not (valores & reales)})


# --------------------------------------------- control de promesas de ganancia

def _plano(texto: str) -> str:
    """Minúsculas y sin acentos (conserva la puntuación), para buscar frases."""
    return sin_acentos(unicodedata.normalize("NFKC", texto or "")).lower()


_FIN_ORACION = re.compile(r"[.!?…]+(?=\s|$)|\n")
_FIN_FRASE = re.compile(r"[.,;:!?…()\n—–]")

# 1) Frases que son una promesa por sí solas. Solo se salvan dentro de una advertencia honesta
#    ("no es dinero fácil", "operar sin riesgo no existe").
_FRASE_PROMESA = re.compile(
    r"\bsin\s+(?:ningun\s+|ningun\s+tipo\s+de\s+|nada\s+de\s+)?riesgos?\b"
    r"|\briesgos?\s+(?:cero|nulo|inexistente)\b|\bcero\s+riesgos?\b|\blibre\s+de\s+riesgos?\b"
    r"|\bno\s+(?:hay|tiene[ns]?|tenes|corre[ns]?|corres|existen?|asumis|asumes)\s+(?:ningun\s+|un\s+|el\s+|mucho\s+|nada\s+de\s+)?riesgos?\b"
    r"|\b(?:dinero|plata|guita|ganancias?)\s+(?:facil|rapid[oa]s?|segur[oa]s?)\b"
    r"|\bmillonari[oa]s?\b|\blibertad\s+financiera\b|\bhacete\s+ric[oa]\b|\bvolvete\s+ric[oa]\b"
    r"|\bingresos?\s+(?:pasivos?|extras?|adicional(?:es)?|mensual(?:es)?|fijos?|constantes?|diarios?|segur[oa]s?)\b"
    r"|\b(?:plata|dinero|guita)\s+extra\b|\bsueldos?\b|\brentas?\b(?!\s+(?:fija|variable))"
)
# 2) Frases de acción que prometen un resultado. No tienen excusa: ni negadas ("nunca nada te
#    multiplicó así el capital") ni en pregunta.
_ACCION_PROMESA = re.compile(
    r"\b(?:dupli|tripli|multipli)(?:c|qu)\w*(?=(?:\s+\w+){0,4}\s+(?:capital|plata|dinero|inversion(?:es)?|cuenta|ahorros?|guita|dolares|usdt|ingresos?|patrimonio)\b)"
    r"|\b(?:capital|plata|dinero|inversion|cuenta|ahorros?|patrimonio)\s+(?:crece|crecen|crecera|crecio|sube|suben|trabaja|trabajando|se\s+multiplica|se\s+duplica)\b"
    r"|\b(?:crecer|crece|trabajar)\s+(?:a\s+)?(?:tu|el|su|mi)\s+(?:capital|plata|dinero|inversion|cuenta|ahorros?|patrimonio)\b"
    r"|\b(?:al|el)\s+doble\b|\bx\s?\d+\b"
    r"|(?<!no\s)\bsiempre\s+gan\w*|\bgan\w+\s+siempre\b|\bgan\w+\s+si\s+o\s+si\b"
    r"|\bnunca\s+(?:mas\s+)?(?:pierd\w*|perd\w*)|\bno\s+(?:podes|puedes|vas\s+a|se\s+puede)\s+perder\b|\bimposible\s+perder\b"
    r"|\bsin\s+perdidas?\b|\bcero\s+perdidas?\b|\bno\s+perdes\b"
    r"|\brecuper\w+\s+(?:tu|la|toda\s+tu)\s+(?:inversion|plata)\b"
    r"|\bvivi[rs]?\s+de(?:l|\s+los|\s+las|\s+tus)?\s+(?:trading|bots?|inversiones|cripto\w*)\b"
)
# 3) Palabras de resultado. Solo valen dentro de una advertencia honesta ("no garantizo ganancias").
_RESULTADO = re.compile(
    r"\bgananc\w*|\bgan(?:a|as|an|ar|ando|aste|amos|aras|aran|aria|arias|aron|e|es|en|o)\b"
    r"|\brendimi\w*|\brind\w*|\brendir\b|\brentab\w*"
    r"|\basegurad[oa]s?\b|\baseguro\b|\basegura\b|\baseguran\b|\baseguramos\b|\basegurar\b"
    r"|\bgaranti\w*|\bretornos?\b|(?<!take )\bprofits?\b"      # ("Take Profit" es el nombre de una orden)
)
# "Ganá un bono", "ganás un reintegro", "ganar tiempo": ahí 'ganar' es un beneficio concreto del producto.
_GANAR_UN_BENEFICIO = re.compile(
    r"(?:\s+[\w$%.,]+){0,6}?\s+(?:bonos?|premios?|reintegros?|cashback|descuentos?|recompensas?|cupon(?:es)?|tiempo|comodidad|experiencia)\b"
)
_RELLENO = (
    r"te|se|lo|la|le|les|nos|me|es|son|esta|estan|hay|existen?|va|van|vas|voy|vamos|a|de|del|que|un|una|el|los|las|"
    r"tu|tus|mi|mis|su|sus|este|esto|eso|ese|bots?|robot|herramienta|sistema|estrategia|inversion|nadie|nada|ningun|ninguna|"
    r"puedo|puede[ns]?|podemos|podes|prometo|promete[ns]?|prometemos|prometer|ofrezco|ofrece[ns]?|ofrecemos|doy|da|dan|damos|"
    r"tengo|tiene[ns]?|significa|implica|garantizo|garantiza[ns]?|garantizamos|garantizar|garantizarte|garantice[ns]?|"
    r"aseguro|asegura[ns]?|aseguramos|asegurar|asegurarte|esperes|espera|esperar|buscas?|buscan|para|quien|quienes|"
    r"ganancias?|resultados?|rendimientos?|rentabilidad|tipo|clase|forma|ni|y|o"
)
# La negación tiene que estar pegada a la palabra: "no te voy a garantizar ganancias" sí,
# "no te pierdas las ganancias aseguradas" no.
_NEGACION = re.compile(
    r"\b(?:no|ni|nadie|nunca|jamas|tampoco|ningun[ao]?s?|nada|sin)\s+(?:(?:" + _RELLENO + r")\s+){0,6}$"
)
# "La rentabilidad no está asegurada", "operar sin riesgo no existe": la negación viene después.
_NEGACION_DESPUES = re.compile(
    r"\s+(?:pasad[oa]s?\s+|futur[oa]s?\s+|anterior(?:es)?\s+)?(?:no|nunca|tampoco)\s+(?:esta[ns]?|es|son|existen?|se\s+puede|garantiza[ns]?|asegura[ns]?)\b"
)
# "Si buscás dinero fácil, esto no es para vos."
_SI_BUSCAS = re.compile(r"\b(?:si\s+(?:buscas|queres|esperas)|quien(?:es)?\s+buscan?|el\s+que\s+busca)\s+(?:\w+\s+){0,2}$")

_PORCENTAJE = re.compile(r"(\d+(?:[.,]\d+)?)?\s*(?:%|por\s+ciento\b)")
# Palabras que, cerca de un porcentaje, lo convierten en una promesa de rendimiento.
_RENDIMIENTO = re.compile(
    r"\b(?:rendimi\w*|rind\w*|gananc\w*|gan[ao]\w*|retorn\w*|rentab\w*|interes(?:es)?|profit\w*|win\s?rate|aciert\w*|"
    r"efectividad|ganador[ae]s?|apy|apr|tna|tea|sub[ei]\w*|crec\w*|rentas?)\b"
)
_PERIODO = re.compile(r"^(?:mensual(?:es)?|anual(?:es)?|diari[oa]s?|semanal(?:es)?|(?:por|al|cada)\s+(?:mes|ano|semana|dia))\b")


def _porcentaje(numero: str) -> str:
    """'3,50' -> '3.5' ; '3' -> '3' (para comparar porcentajes sin importar cómo estén escritos)."""
    numero = numero.replace(",", ".")
    return numero.rstrip("0").rstrip(".") if "." in numero else numero


def _advertencia_honesta(plano: str, inicio: int, fin: int) -> bool:
    """¿La frase prohibida está dicha como advertencia? ('no es dinero fácil', 'nadie garantiza ganancias')"""
    corte = _FIN_ORACION.search(plano, fin)
    if corte and "?" in corte.group(0):
        return False                               # "¿No es dinero fácil?" es un gancho, no una advertencia
    antes = _FIN_FRASE.split(plano[:inicio])[-1]
    if _NEGACION.search(antes):
        return True
    resto = plano[fin : corte.start() if corte else len(plano)]
    if _NEGACION_DESPUES.match(resto):
        return True
    return bool(_SI_BUSCAS.search(antes) and re.search(r"\b(?:no|nunca|tampoco)\b", resto))


def promesas(texto: str, datos: str = "") -> list[str]:
    """Frases de promesa de ganancia que aparecen en un texto de venta (tienen que ser cero).

    - Palabras de resultado (ganancia, rendimiento, asegurado, garantizado...) y frases como
      "sin riesgo" o "dinero fácil": solo pasan dentro de una advertencia honesta.
    - Frases que prometen un resultado ("duplicá tu capital", "no podés perder"): no pasan nunca.
    - Un porcentaje solo pasa si es un dato del producto: tiene que figurar tal cual en los DATOS de
      la promo (un reintegro, un descuento) y no estar al lado de palabras de rendimiento."""
    plano = _plano(texto)
    malas: set[str] = set()
    for m in _ACCION_PROMESA.finditer(plano):
        malas.add(m.group(0))
    for m in _FRASE_PROMESA.finditer(plano):
        if not _advertencia_honesta(plano, m.start(), m.end()):
            malas.add(m.group(0))
    for m in _RESULTADO.finditer(plano):
        if m.group(0).startswith("gan") and not m.group(0).startswith("gananc") and _GANAR_UN_BENEFICIO.match(plano, m.end()):
            continue
        if not _advertencia_honesta(plano, m.start(), m.end()):
            malas.add(m.group(0))

    permitidos = {_porcentaje(m.group(1)) for m in _PORCENTAJE.finditer(_plano(datos)) if m.group(1)}
    for m in _PORCENTAJE.finditer(plano):
        desde = 0
        for corte in _FIN_ORACION.finditer(plano, 0, m.start()):
            desde = corte.end()
        corte = _FIN_ORACION.search(plano, m.end())
        antes = " ".join(plano[desde : m.start()].split()[-4:])
        despues = plano[m.end() : corte.start() if corte else len(plano)].split()
        numero = m.group(1) or ""
        cerca = antes + " % " + " ".join(despues[:4])
        if (not numero or _porcentaje(numero) not in permitidos or _RENDIMIENTO.search(cerca)
                or _PERIODO.match(" ".join(despues[:2]))):
            malas.add(f"{numero}%")
    return sorted(malas)


def anio_actual(momento: datetime) -> set[str]:
    return {str(momento.year)}


# --------------------------------------------------------------- limpieza

_LUTO = re.compile(
    r"(?i)\b(muert[oa]s?|muertes?|fallecid[oa]s?|fallecimiento|v[ií]ctimas?|asesinad[oa]s?|masacre|tragedia|"
    r"herid[oa]s?|atentado)\b"
)


def _texto(valor) -> str:
    """Solo se acepta texto (o un número suelto). Si la IA manda una lista o un objeto donde iba
    una frase, queda vacío: mejor que falte un dato a publicar algo roto."""
    if isinstance(valor, bool) or valor is None:
        return ""
    if isinstance(valor, (int, float)):
        return str(valor)
    return valor if isinstance(valor, str) else ""


def _lista(valor) -> list:
    if isinstance(valor, list):
        return valor
    return [valor] if isinstance(valor, str) and valor.strip() else []


def _limpio(valor) -> str:
    t = re.sub(r"\s+", " ", _texto(valor)).strip()
    return sin_links(t)


_TERMINACION = re.compile(
    r"(?i)\." + ESPACIO_INVISIBLE + r"?(?:com|net|org|io|co|info|tv|gov|gob)"
    r"(?:\." + ESPACIO_INVISIBLE + r"?(?:ar|es|mx|cl|uy|br|pe|us|uk))?(?![^\W_])"
    r"|\." + ESPACIO_INVISIBLE + r"?(?:ar|es|mx|cl|uy|br|pe|us|uk)$"
)


def nombre_de_fuente(nombre: str) -> str:
    """'Investing.com' -> 'Investing' ; 'lanacion.com.ar' -> 'Lanacion' ; 'es.investing.com' -> 'Investing' ;
    'Investing.com Español' -> 'Investing Español'."""
    original = nombre.strip()
    limpio = _TERMINACION.sub("", original, count=1)
    if limpio == original:
        return original                                  # no era una dirección web: se deja como está
    limpio = re.sub(r"\s+", " ", limpio).strip(" .")
    primera, _, resto = limpio.partition(" ")
    primera = primera.replace(ESPACIO_INVISIBLE, "")
    if "." in primera:
        primera = primera.rsplit(".", 1)[-1]             # 'es.investing' -> 'investing'
    if primera.islower():
        primera = primera[:1].upper() + primera[1:]
    return f"{primera} {resto}".strip()


def fuente_limpia(nombre) -> str:
    """El nombre de un medio listo para publicar: sin nada que se convierta en link."""
    return nombre_de_fuente(sin_links(re.sub(r"\s+", " ", _texto(nombre)).strip()))


def _si(valor) -> bool:
    return valor is True or str(valor).strip().lower() in ("true", "si", "sí", "1", "yes")


def normalizar_post(crudo: dict, categoria: str, fuente_respaldo: str) -> dict:
    """Ordena y limpia lo que devolvió la IA para una noticia."""
    respaldo = fuente_limpia(fuente_respaldo)
    post = {
        "titular": _limpio(crudo.get("titular")).rstrip("."),
        "titular_imagen": _limpio(crudo.get("titular_imagen")).rstrip("."),
        "resumen": _limpio(crudo.get("resumen")),
        "dato_clave": _limpio(crudo.get("dato_clave")),
        "por_que_importa": _limpio(crudo.get("por_que_importa")),
        "pregunta": _limpio(crudo.get("pregunta")),
        "bandera": _texto(crudo.get("bandera")).strip()[:8],
        "tono": _texto(crudo.get("tono")).strip().lower() or "neutral",
        "fuente": fuente_limpia(crudo.get("fuente")) or respaldo,
        "fotos": [_texto(f).strip() for f in _lista(crudo.get("fotos")) if _texto(f).strip()][:4],
        "escena": _limpio(crudo.get("escena"))[:240],
        "llamado": _si(crudo.get("llamado")),
        "para_la_marca": _texto(crudo.get("para_la_marca")).strip().lower(),
        "categoria": categoria,
    }
    if post["tono"] not in ("alcista", "bajista", "neutral"):
        post["tono"] = "neutral"
    if post["para_la_marca"] not in ("buena", "neutra", "mala"):
        post["para_la_marca"] = ""
    if not re.fullmatch(r"[\U0001F1E6-\U0001F1FF]{2}", post["bandera"]):
        post["bandera"] = ""               # solo se acepta una bandera, nada más
    if not post["titular_imagen"]:
        post["titular_imagen"] = post["titular"]
    if _LUTO.search(post["titular"] + " " + post["resumen"]):
        post["pregunta"] = ""              # con muertos o heridos no se le pide a la gente que vote ni opine
        post["llamado"] = False
    return post


def texto_revisable(post: dict) -> str:
    campos = ("titular", "titular_imagen", "resumen", "dato_clave", "por_que_importa", "pregunta")
    return " \n ".join(post.get(c, "") for c in campos)


def faltantes(post: dict) -> list[str]:
    return [c for c in ("titular", "resumen", "por_que_importa") if len(post.get(c, "")) < 12]


# --------------------------------------------------- llamado a registrarse

def armar_llamado(socio: dict | None, cfg: dict, texto: str = "", enlace: str = "", aviso: str | None = None,
                  sin_link: str = "", paso_2: dict | None = None) -> dict | None:
    """El 'registrate con mi link': texto, link y aviso. Los datos salen del socio; una promo puede
    traer los suyos (su propio link, su propio aviso y qué decir en X cuando no se pone el link)."""
    socio = socio or {}
    # Estos textos vienen de ajustes.yaml: también se limpian, para que "Registrate en Pionex.com"
    # no termine siendo un segundo link (el único link es el tuyo, que va aparte).
    texto = sin_links(str(texto or socio.get("llamado") or "").strip())
    enlace = str(enlace or socio.get("enlace") or "").strip() or str(cfg.get("enlace_general") or "").strip()
    if not texto or not enlace:
        return None
    aviso = socio.get("aviso") if aviso is None else aviso
    llamado = {"texto": texto, "enlace": enlace, "aviso": sin_links(str(aviso or "").strip()), "sin_link": sin_links(str(sin_link or "").strip())}
    # Paso 2 (opcional): después de registrarse con tu link, entrar a la campaña o al evento.
    if isinstance(paso_2, dict):
        texto_2 = sin_links(str(paso_2.get("texto") or "").strip())
        enlace_2 = str(paso_2.get("enlace") or "").strip()
        if texto_2 and enlace_2.startswith("http"):
            llamado["paso_2"] = {"texto": texto_2, "enlace": enlace_2}
    return llamado


def _llamado_telegram(ll: dict) -> tuple[str, str]:
    """Devuelve (con formato, lo que se ve)."""
    a = lambda enlace, texto: f'<a href="{html.escape(enlace, quote=True)}">{html.escape(texto, quote=False)}</a>'
    if ll.get("paso_2"):
        p2 = ll["paso_2"]
        linea = f"👉 1) {a(ll['enlace'], ll['texto'])}\n👉 2) {a(p2['enlace'], p2['texto'])}"
        visible = f"👉 1) {ll['texto']}\n👉 2) {p2['texto']}"
    else:
        linea = f"👉 {a(ll['enlace'], ll['texto'])}"
        visible = f"👉 {ll['texto']}"
    if ll["aviso"]:
        linea += f"\n<i>{html.escape(ll['aviso'], quote=False)}</i>"
        visible += f"\n{ll['aviso']}"
    return linea, visible


def link_en_x(cfg_x: dict, es_promo: bool) -> bool:
    """¿Va el link escrito en el post de X? ('poner_links': false · promos · siempre)"""
    modo = cfg_x.get("poner_links")
    if isinstance(modo, bool):
        return modo
    modo = str(modo or "").strip().lower()
    return modo in ("siempre", "todo", "todos", "si", "sí") or (es_promo and modo == "promos")


def _llamado_x(ll: dict, cfg_x: dict, es_promo: bool = False) -> str:
    texto = ll["texto"]
    if ll.get("paso_2"):
        segundo = ll["paso_2"]["texto"]
        texto = f"{texto.rstrip('.')} y {segundo[:1].lower()}{segundo[1:]}"
    if link_en_x(cfg_x, es_promo):
        return f"👉 {texto}: {ll['enlace']}"
    donde = ll.get("sin_link") or sin_links(str(cfg_x.get("texto_sin_link") or "el link está en mi perfil"))
    if ll.get("paso_2") and not ll.get("sin_link"):
        donde = "los links están en mi perfil"
    return f"👉 {texto} ({donde})"


# ------------------------------------------------------------- noticias

def _simbolo(post: dict) -> str:
    return post["bandera"] or EMOJI_CATEGORIA.get(post["categoria"], "📰")


def texto_telegram(post: dict, urgente: bool, llamado: dict | None = None, redes: dict | None = None) -> str:
    """Pie de foto para Telegram (con negritas)."""
    e = lambda s: html.escape(s, quote=False)

    def armar(con_pregunta: bool, resumen: str) -> tuple[str, int]:
        lineas, visibles = [], []
        if urgente:
            lineas.append("🚨 <b>ÚLTIMA HORA</b>")
            visibles.append("🚨 ÚLTIMA HORA")
        lineas.append(f"<b>{_simbolo(post)} {e(post['titular'])}</b>")
        visibles.append(f"{_simbolo(post)} {post['titular']}")
        lineas += ["", e(resumen), "", f"💡 <b>Por qué importa:</b> {e(post['por_que_importa'])}"]
        visibles += ["", resumen, "", f"💡 Por qué importa: {post['por_que_importa']}"]
        if llamado:
            con_formato, visible = _llamado_telegram(llamado)
            lineas += ["", con_formato]
            visibles += ["", visible]
        elif con_pregunta and post["pregunta"]:
            lineas += ["", e(post["pregunta"])]
            visibles += ["", post["pregunta"]]
        lineas += ["", f"📰 Fuente: {e(post['fuente'])}"]
        visibles += ["", f"📰 Fuente: {post['fuente']}"]
        if redes:
            # Al pie: el link a todas tus redes (tu Linktree).
            lineas.append(f'📲 <a href="{html.escape(redes["enlace"], quote=True)}">{e(redes["texto"])}</a>')
            visibles.append(f"📲 {redes['texto']}")
        return "\n".join(lineas), len("\n".join(visibles))

    texto, largo = armar(True, post["resumen"])
    if largo > LIMITE_TELEGRAM:
        texto, largo = armar(False, post["resumen"])
    resumen = post["resumen"]
    while largo > LIMITE_TELEGRAM and " " in resumen:
        resumen = resumen.rsplit(" ", 8)[0].rstrip(" ,;:") + "…"
        texto, largo = armar(False, resumen)
    return texto


def texto_x(post: dict, urgente: bool, llamado: dict | None = None, cfg_x: dict | None = None) -> str:
    """Texto para X: prueba de la versión más completa a la más corta hasta que entre.
    El llamado nunca sale sin su aviso de riesgo: si los dos juntos no entran, sale sin llamado."""
    cabeza = f"{_simbolo(post)} {post['titular']}"
    if urgente:
        cabeza = "🚨 ÚLTIMA HORA: " + cabeza
    fuente = f"Fuente: {post['fuente']}"
    dato = post["dato_clave"] or post["por_que_importa"]
    palabras = fichas(dato)
    if palabras and len(palabras & fichas(post["titular"])) / len(palabras) >= 0.6:
        dato = post["por_que_importa"]            # si el dato repite el titular, mejor el "por qué importa"
    sin_llamado = [
        [cabeza, dato, post["pregunta"], fuente],
        [cabeza, dato, fuente],
        [cabeza, post["pregunta"], fuente],
        [cabeza, fuente],
    ]
    if llamado:
        linea = _llamado_x(llamado, cfg_x or {})
        aviso = f"⚠️ {llamado['aviso']}" if llamado["aviso"] else ""
        opciones = [
            [cabeza, dato, linea, aviso, fuente],
            [cabeza, linea, aviso, fuente],
        ] + sin_llamado[1:]
    else:
        opciones = sin_llamado
    elegido = _primera_que_entra(opciones)
    if elegido:
        return elegido
    resto = LIMITE_X - peso_x("\n\n" + fuente)
    return recortar_x(cabeza, resto) + "\n\n" + fuente


# ------------------------------------------- pedido de imagen para ChatGPT

def idea_de_imagen(post: dict, urgente: bool, marca: str = "", protagonista: str = "") -> str:
    """Un pedido listo para pegar en ChatGPT y que arme la imagen de esa noticia (para cuando la
    imagen la hace el dueño a mano). La escena no incluye personas reales.
    'protagonista' es la marca de la noticia (Binance, Exness...), para pedir que se vea su logo."""
    cartel = '"ÚLTIMA HORA", en rojo y con una sirena' if urgente else f'"{post["categoria"]}"'
    partes = [
        "Haceme una imagen cuadrada (1:1) estilo miniatura de noticia para redes: fotorrealista (nada de dibujito), "
        "muy llamativa, colores intensos, luz dramática y look 3D brillante.",
        f"Escena: {(post['escena'] or 'símbolos del mundo financiero y cripto relacionados con la noticia').rstrip('.')}.",
        f"Que se vea bien el logo oficial de {protagonista}." if protagonista else "",
        f'Texto grande en la mitad de abajo, en mayúsculas, con letras gruesas y bien legibles: "{post["titular_imagen"].upper()}". '
        "Repartilo en 2 o 3 renglones: letras blancas con relieve y la cifra o la palabra más importante en amarillo dorado.",
        f"Arriba a la izquierda, un cartel que diga {cartel}.",
    ]
    if marca:
        partes.append(f'Abajo de todo, centrado, chiquito y con letras separadas: "{marca}".')
    partes.append("Escribí los textos exactamente como te los paso, sin cambiar ni una letra ni un número. Sin marcas de agua.")
    return "\n".join(p for p in partes if p)


# --------------------------------------------------------------- promos

def normalizar_promo(crudo: dict) -> dict:
    """Ordena y limpia lo que devolvió la IA para una promo."""
    puntos = [_limpio(p).rstrip(".") for p in _lista(crudo.get("puntos"))]
    titular = _limpio(crudo.get("titular")).rstrip(".")
    return {
        "titular": titular,
        "titular_imagen": _limpio(crudo.get("titular_imagen")).rstrip(".") or titular,
        "texto": _limpio(crudo.get("texto")),
        "puntos": [p for p in puntos if 3 < len(p) <= 60][:3],
    }


def texto_revisable_promo(p: dict) -> str:
    return " \n ".join([p["titular"], p["titular_imagen"], p["texto"], *p["puntos"]])


def faltantes_promo(p: dict) -> list[str]:
    faltan = [c for c in ("titular", "texto") if len(p.get(c, "")) < 12]
    if len(p.get("puntos", [])) < 2:
        faltan.append("puntos")
    return faltan


def enlaces_extra(promo_cfg: dict) -> list[dict]:
    """Los links de más que una promo puede llevar en Telegram (un dossier, un tutorial)."""
    extras = []
    for item in _lista(promo_cfg.get("extras")):
        if isinstance(item, dict) and str(item.get("texto") or "").strip() and str(item.get("enlace") or "").strip().startswith("http"):
            extras.append({"texto": sin_links(str(item["texto"]).strip()), "enlace": str(item["enlace"]).strip(),
                           "emoji": str(item.get("emoji") or "📎").strip()[:4] or "📎"})
    return extras[:3]


def _falta(dato: str, *textos: str) -> bool:
    """¿El dato que no puede faltar (un código de descuento, una fecha) todavía no está escrito?"""
    return bool(dato) and normalizar(dato) not in normalizar(" ".join(textos))


def promo_telegram(p: dict, llamado: dict, emoji: str = "📢", extras: list[dict] | None = None, dato_fijo: str = "") -> str:
    e = lambda s: html.escape(s, quote=False)
    dato_fijo = sin_links(dato_fijo.strip())

    def armar(texto: str) -> tuple[str, int]:
        con_formato, visible = _llamado_telegram(llamado)
        lineas = [f"<b>{emoji} {e(p['titular'])}</b>", "", e(texto), ""]
        visibles = [f"{emoji} {p['titular']}", "", texto, ""]
        lineas += [f"✅ {e(punto)}" for punto in p["puntos"]]
        visibles += [f"✅ {punto}" for punto in p["puntos"]]
        if _falta(dato_fijo, p["titular"], texto, *p["puntos"]):
            lineas += ["", f"<b>{e(dato_fijo)}</b>"]
            visibles += ["", dato_fijo]
        lineas += ["", con_formato]
        visibles += ["", visible]
        for extra in extras or []:
            simbolo = extra.get("emoji") or "📎"
            lineas.append(f'{simbolo} <a href="{html.escape(extra["enlace"], quote=True)}">{e(extra["texto"])}</a>')
            visibles.append(f"{simbolo} {extra['texto']}")
        return "\n".join(lineas), len("\n".join(visibles))

    texto = p["texto"]
    salida, largo = armar(texto)
    while largo > LIMITE_TELEGRAM and " " in texto:
        texto = texto.rsplit(" ", 8)[0].rstrip(" ,;:") + "…"
        salida, largo = armar(texto)
    return salida


def promo_x(p: dict, llamado: dict, cfg_x: dict, emoji: str = "📢", dato_fijo: str = "") -> str:
    """El llamado, su aviso de riesgo y el dato que no puede faltar (un código de descuento, por
    ejemplo) van siempre: si hace falta, se acorta el resto."""
    cabeza = f"{emoji} {p['titular']}"
    linea = _llamado_x(llamado, cfg_x, es_promo=True)
    aviso = f"⚠️ {llamado['aviso']}" if llamado["aviso"] else ""
    dato_fijo = sin_links(dato_fijo.strip())
    primera = re.split(r"(?<=[.!?])\s+", p["texto"].strip())[0] if p["texto"] else ""

    def con_dato(*cuerpo: str) -> list[str]:
        return [*cuerpo, dato_fijo if _falta(dato_fijo, *cuerpo, linea) else "", linea, aviso]

    elegido = _primera_que_entra([
        con_dato(cabeza, p["texto"]),
        con_dato(cabeza, primera),                # si no entra todo, al menos la primera oración
        con_dato(cabeza),
    ])
    if elegido:
        return elegido
    cierre = "\n\n".join(parte for parte in con_dato()[0:] if parte)
    return recortar_x(cabeza, LIMITE_X - peso_x("\n\n" + cierre)) + "\n\n" + cierre


# ------------------------------------------------ textos propios de Eze

_LINK_MD = re.compile(r"\[([^\]\n]+)\]\((https?://[^)\s]+)\)")
_LINK_SUELTO = re.compile(r"https?://[^\s<>()\[\]]+")


def limpiar_enlace(enlace: str) -> str:
    """Saca el rastreo que agregan otras herramientas (?utm_source=chatgpt.com y parecidos)."""
    base, _, consulta = enlace.partition("?")
    resto = [p for p in consulta.split("&") if p and not p.lower().startswith("utm_")]
    return base + ("?" + "&".join(resto) if resto else "")


def texto_propio_telegram(texto: str) -> str:
    """El texto que escribió Eze, tal cual, listo para Telegram. Los links se pueden escribir
    sueltos (https://...) o como [texto](https://...): en ese caso se ve solo el texto, con el link."""
    partes, pos = [], 0
    for m in _LINK_MD.finditer(texto):
        partes.append(_escapar_con_links(texto[pos:m.start()]))
        partes.append(f'<a href="{html.escape(limpiar_enlace(m.group(2)), quote=True)}">{html.escape(m.group(1), quote=False)}</a>')
        pos = m.end()
    partes.append(_escapar_con_links(texto[pos:]))
    salida = "".join(partes)
    salida = re.sub(r"[ \t]+\n", "\n", salida)
    return re.sub(r"\n{3,}", "\n\n", salida).strip()


def _escapar_con_links(trozo: str) -> str:
    partes, pos = [], 0
    for m in _LINK_SUELTO.finditer(trozo):
        partes.append(html.escape(trozo[pos:m.start()], quote=False))
        partes.append(html.escape(limpiar_enlace(m.group(0)), quote=False))
        pos = m.end()
    partes.append(html.escape(trozo[pos:], quote=False))
    return "".join(partes)


def texto_propio_x(texto_x: str, texto_largo: str, cfg_x: dict) -> str:
    """La versión corta para X. Si no hay una escrita, va la primera línea del texto largo y el
    aviso de que el link está en el perfil. Nunca lleva links (X cobra más por cada post con link)."""
    corto = str(texto_x or "").strip()
    if not corto:
        primera = next((l.strip() for l in _LINK_MD.sub(r"\1", texto_largo).splitlines() if l.strip()), "")
        donde = sin_links(str(cfg_x.get("texto_sin_link") or "el link está en mi perfil"))
        corto = f"{primera}\n\n👉 {donde[:1].upper()}{donde[1:]}"
    corto = sin_links(_LINK_MD.sub(r"\1", corto))
    return recortar_x(corto)


# ------------------------------------------------ X Premium: posts largos

LIMITE_X_PREMIUM = 25000


def x_premium(cfg_x: dict) -> bool:
    from .util import verdadero
    return verdadero(cfg_x.get("premium"))


def x_largo(texto_html: str, cfg_x: dict, con_links: bool) -> str:
    """El mismo texto de Telegram, para una cuenta de X con Premium (acepta posts largos).
    Los links van escritos solo si 'poner_links' lo permite (X cobra más por cada post con link);
    si no, se cambian por 'link en mi perfil'."""
    donde = sin_links(str(cfg_x.get("texto_sin_link") or "el link está en mi perfil"))

    def enlace(m: re.Match) -> str:
        url, visible = html.unescape(m.group(1)), html.unescape(re.sub(r"<[^>]+>", "", m.group(2)))
        return f"{visible}: {url}" if con_links else f"{visible} ({donde})"

    t = re.sub(r'<a href="([^"]+)">(.*?)</a>', enlace, texto_html, flags=re.S)
    t = html.unescape(re.sub(r"<[^>]+>", "", t))
    if not con_links:
        t = _URL.sub(f"({donde})", t)
        t = t.replace(f"({donde}) ({donde})", f"({donde})")
    t = re.sub(r"\n{3,}", "\n\n", t).strip()
    return t[:LIMITE_X_PREMIUM]


# ------------------------------------------------ charla en X

_CUENTO_REAL = re.compile(r"(?i)\b(conoc[ií]|habl[eé] con|me cont[oó]|un amigo|una amiga|un alumno|una alumna|un seguidor|mi primo|mi vecino)\b")


def revisar_charla(crudo: dict, anteriores: list[str]) -> tuple[str, list[str]]:
    """Limpia el texto de una charla y devuelve (texto, problemas). Sin problemas = se puede publicar."""
    texto = crudo.get("texto") if isinstance(crudo, dict) else ""
    texto = sin_links(str(texto or "")).strip().strip('"').strip()
    texto = re.sub(r"\n{3,}", "\n\n", texto)
    problemas = []
    if len(texto) < 25:
        problemas.append("quedó vacía o demasiado corta")
    if peso_x(texto) > 280:
        problemas.append("es más larga que 280 caracteres")
    if _CUENTO_REAL.search(texto):
        problemas.append("cuenta un caso como si fuera real (tiene que ser una hipótesis: 'imaginá...')")
    malas = promesas(texto, "")
    if malas:
        problemas.append(f"suena a promesa de ganancia ({', '.join(malas)})")
    clave = normalizar(texto)[:60]
    if any(normalizar(a)[:60] == clave for a in anteriores):
        problemas.append("repite una publicación anterior")
    return texto, problemas
