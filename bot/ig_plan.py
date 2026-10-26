"""Qué carrusel de Instagram toca y qué dice cada lámina.

Se rotan TODOS los estilos (titular, editorial, partida) y todos los tipos de contenido (enseñanza,
errores y hábitos, mentalidad, bots e IA). Ninguno reemplaza a otro: cuando Instagram esté conectado,
se guardan los me gusta, comentarios y guardados de cada uno para ver cuáles funcionan más."""
from __future__ import annotations

import random
import re

from . import ia

# (tipo de contenido, estilo). La rueda pasa por todos antes de repetir.
RUEDA = [
    # Por ahora, solo el estilo negro y verde (el del carrusel de Bitcoin): los de papel y los de pantalla
    # partida con fotos de Eze quedan apagados (no le gustaron, oct-2026).
    ("ensenanza", "titular"),
    ("errores", "titular"),
    ("ensenanza", "titular"),
    ("bots", "titular"),
    ("habitos", "titular"),
    ("ensenanza", "titular"),
    ("mentalidad", "titular"),
]

TEMAS = {
    "ensenanza": [
        "¿Qué es Bitcoin?", "¿Qué es la blockchain?", "¿Qué es USDT y para qué sirve una stablecoin?",
        "¿Qué es una criptomoneda?", "¿Qué es una billetera cripto (wallet) y cuál conviene?",
        "¿Qué es la frase semilla (seed phrase) y por qué nunca hay que compartirla?",
        "¿Qué es un exchange y cómo elegir uno?", "¿Qué es el halving de Bitcoin?",
        "¿Qué es Ethereum y qué es un contrato inteligente?", "¿Qué es DeFi?",
        "¿Qué es un ETF de Bitcoin?", "¿Qué es una altcoin y qué es una memecoin?",
        "¿Qué es un airdrop?", "¿Qué es el trading?", "Trading vs. invertir: ¿cuál es la diferencia?",
        "¿Qué es el stop loss?", "¿Qué es el apalancamiento y por qué es peligroso?",
        "¿Qué es el spread?", "¿Qué es forex y qué es un par de divisas (EUR/USD, AUD/CAD)?",
        "¿Qué es un bot de trading?", "¿Cómo se usa la IA en el trading?",
        "¿Qué es la gestión de riesgo (la regla del 1%)?", "¿Qué es el interés compuesto?",
        "¿Qué es la inflación y por qué te hace perder dinero?", "¿Qué es una acción y qué es el S&P 500?",
        "¿Cómo detectar una estafa cripto?", "¿Qué es el análisis técnico?", "¿Qué es una vela japonesa?",
        "¿Qué es el calendario económico y por qué mueve el mercado?", "¿Qué es una orden límite y una orden a mercado?",
    ],
    "errores": [
        "errores que te hacen perder dinero en trading", "errores al comprar tu primera cripto",
        "errores al usar un bot de trading", "errores de gestión de riesgo", "errores al elegir un exchange",
    ],
    "habitos": [
        "hábitos de los traders que duran años", "hábitos para ordenar tu dinero",
        "hábitos para cuidar tus criptos", "hábitos para no operar con la emoción",
    ],
    "mentalidad": [
        "trading vs. apostar", "paciencia vs. impulso", "culpar al mercado vs. hacerse cargo",
        "aprender vs. buscar atajos", "ganar rápido vs. durar en el mercado",
    ],
    "bots": [
        "cómo decide un bot de trading cuándo entrar", "qué mira un bot antes de operar EUR/AUD y AUD/CAD",
        "lo que un bot hace mejor que una persona (y lo que no)", "cómo usar la IA para analizar el mercado",
    ],
}

PALABRAS_CIERRE = {"ensenanza": "GUÍA", "errores": "ERRORES", "habitos": "HÁBITOS", "mentalidad": "PLAN", "bots": "BOT"}

SISTEMA = """Armás carruseles de Instagram para Ezequiel Bagnato (@ezebagnato): trader, bots de trading, IA y cripto, para gente de Argentina y Latinoamérica que recién empieza.
Español rioplatense con voseo, claro y concreto. Decí siempre "dinero", nunca "plata".
Escribí con ortografía perfecta: tildes, comas y puntos donde van, y oraciones completas que se entiendan solas (nada de frases sueltas o pegadas sin sentido). Antes de responder, releé cada texto como si fueras un corrector.
Tienen que ENSEÑAR DE VERDAD: nada de frases vagas. Cada lámina explica una idea con un dato concreto, un ejemplo con números o una comparación de la vida real.
Nunca prometas ganancias, ni "sin riesgo", ni porcentajes de ganancia, ni recomiendes comprar o vender algo concreto. No inventes casos reales.
Solo datos ampliamente conocidos y estables (fechas, definiciones, reglas del protocolo). Si no estás seguro de un número, no lo pongas. Nada de afirmaciones discutibles como si fueran hechos.
Postura de Eze sobre dónde guardar cripto: para empezar, un exchange grande y regulado (PSAV en Argentina, con 2FA) es lo más práctico; la billetera propia es para quien sabe usarla, porque si perdés la frase semilla o te hackean nadie te devuelve el dinero. No digas que el exchange es inseguro ni uses "not your keys, not your coins" como regla absoluta.
Los números siempre con cifras (21 millones, no "veintiún millones"). Nunca pongas estadísticas del tipo "el 70% de los traders pierde": no se pueden verificar. En la caja "dato" va un hecho comprobable, una cuenta (ej.: 2% de riesgo por 10 pérdidas seguidas deja la cuenta en 82%) o un tip práctico.
Marcá entre *asteriscos* la palabra o frase corta más importante de cada titular y de cada texto (se pinta de color).

ESTILO:
- titular: portada {"tipo":"portada","etiqueta":"2-3 palabras","titulo":"máx. 6 palabras, con *clave*","bajada":"máx. 12 palabras","foto":"búsqueda EN INGLÉS de una foto real del tema (ej. 'Bitcoin mining farm', 'New York Stock Exchange')"}.
  Después 4 a 5 láminas {"tipo":"paso","etiqueta":"1-3 palabras (ej. 'Cómo funciona')","titulo":"máx. 6 palabras, con *clave*","texto":"30 a 45 palabras, con un dato o ejemplo","dato":"opcional: un hecho o número verificable en 1 oración","foto":"opcional: búsqueda en inglés de una foto real (solo en 1 o 2 láminas)"}.
- editorial: portada {"tipo":"portada","numero":"cuántos puntos","titulo":"máx. 5 palabras","banda":"la frase clave con *resaltado*","foto":"búsqueda en inglés"}.
  Después 5 láminas {"tipo":"punto","titulo":"máx. 5 palabras con punto final y *clave*","lista":["2 o 3 renglones de máx. 7 palabras, concretos"],"banda":"una frase que remata con *resaltado*"}.
- partida: 4 láminas de contraste {"arriba":"lo que hace la mayoría (máx. 8 palabras, con *clave*)","abajo":"lo que hace el trader que dura (máx. 8 palabras, con *clave*)"}. Ej.: arriba "Culpar al *mercado* cuando perdés", abajo "Revisar *qué hiciste vos* mal".

Respondé SOLO con JSON:
{"laminas": [...], "texto_post": "gancho fuerte de 2 líneas, la idea en 3 o 4 líneas con un dato, y al final: 📌 Guardalo / ✈️ Mandáselo a alguien que lo necesite / 🔔 Seguime para más", "palabra": "UNA PALABRA CLAVE DEL TEMA"}
No incluyas la lámina de cierre: la agrega el sistema."""


def sin_tildes(texto: str) -> str:
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn")


def que_toca(memoria: dict) -> tuple[str, str, str]:
    """(tipo, estilo, tema) del próximo carrusel, sin repetir tema hasta agotar la lista."""
    vuelta = int(memoria.get("vuelta", 0) or 0)
    tipo, estilo = RUEDA[vuelta % len(RUEDA)]
    usados = set(memoria.get("temas_usados") or [])
    libres = [t for t in TEMAS[tipo] if t not in usados] or TEMAS[tipo]
    return tipo, estilo, libres[0] if tipo == "ensenanza" else random.choice(libres)


def _sin_problemas(textos: list[str]) -> list[str]:
    from .texto import promesas_charla
    problemas = []
    if any(re.search(r"(?i)\bplata\b", t) for t in textos):
        problemas.append('dice "plata"')
    # Estadísticas de "cuántos traders pierden" y similares: suenan a dato pero casi siempre son inventadas.
    if any(re.search(r"(?i)(\d+\s*%|por ciento)\s+de\s+(los\s+|las\s+)?(traders|personas|inversores|principiantes|usuarios|argentinos)", t) for t in textos):
        problemas.append("pusiste una estadística de cuánta gente hace o pierde algo (no se puede verificar: sacala)")
    if any(re.search(r"(?i)\b(veinti|treinta|cuarenta|cincuenta|sesenta|setenta|ochenta|noventa)\w*\b|\b(dos|tres|cinco|diez) por ciento\b", t) for t in textos):
        problemas.append("escribiste números con letras (van con cifras: 2%, 21 millones)")
    malas = [m for t in textos for m in promesas_charla(t)]
    if malas:
        problemas.append(f"suena a promesa ({', '.join(malas)})")
    return problemas


def redactar(cerebro, tipo: str, estilo: str, tema: str) -> dict | None:
    """Pide el contenido a la IA (hasta 2 intentos) y lo devuelve listo para ig_carruseles."""
    cantidad = {"titular": "portada + 5 pasos", "editorial": "portada + 5 puntos", "partida": "4 láminas de contraste"}[estilo]
    correccion = ""
    for _ in range(2):
        usuario = f"ESTILO: {estilo}\nTIPO: {tipo}\nTEMA: {tema}\nCANTIDAD: {cantidad}"
        if correccion:
            usuario += f"\n\nATENCIÓN: tu intento anterior no sirve: {correccion}."
        try:
            crudo = cerebro.pedir_json(SISTEMA, usuario, max_tokens=3000, temperatura=0.6)
        except ia.SinCerebro:
            return None
        laminas = [l for l in (crudo.get("laminas") or []) if isinstance(l, dict)]
        textos = [str(v) for l in laminas for v in l.values() if isinstance(v, str)]
        textos += [str(x) for l in laminas for x in (l.get("lista") or [])] + [str(crudo.get("texto_post") or "")]
        problemas = _sin_problemas(textos)
        if len(laminas) >= 2 and not problemas:
            palabra = re.sub(r"[^\wÁÉÍÓÚÑáéíóúñ]", "", str(crudo.get("palabra") or PALABRAS_CIERRE[tipo])).upper()[:10] or PALABRAS_CIERRE[tipo]
            return {"laminas": laminas, "texto_post": str(crudo.get("texto_post") or "").strip(), "palabra": palabra}
        correccion = "; ".join(problemas) or "faltan láminas"
    return None


def logo_del_tema(tema: str):
    """El logo oficial si el tema es sobre una moneda o empresa que está guardada (Bitcoin, Ethereum, USDT...)."""
    from .imagen import logo_guardado
    from .util import normalizar
    t = normalizar(tema)
    for palabra in re.findall(r"[a-z0-9]+", t):
        if len(palabra) >= 3 and palabra not in ("que", "es", "una", "como", "por", "para", "sirve"):
            foto = logo_guardado(palabra)
            if foto is not None:
                return foto.imagen
    return None


def armar(contenido: dict, estilo: str, fotos: list | None = None, usuario: str = "@ezebagnato", tema: str = "") -> list[bytes]:
    """Las imágenes del carrusel (con la lámina de cierre). 'fotos' = fotos de Eze para el estilo partida."""
    from . import ig_carruseles as c
    laminas = [dict(l) for l in contenido["laminas"]]
    palabra = contenido["palabra"]
    logo = logo_del_tema(tema) if tema else None
    if estilo == "titular":
        if laminas and laminas[0].get("tipo") == "portada" and logo is not None:
            laminas[0]["logo"] = logo
        laminas.append({"tipo": "cierre", "palabra": palabra, "texto": "y te mando la guía completa por privado",
                        "extra": "Operar tiene riesgo. Nada de esto es una recomendación de inversión."})
        return c.titular(laminas, usuario)
    if estilo == "editorial":
        if laminas and laminas[0].get("tipo") != "portada":
            laminas[0]["tipo"] = "portada"
        laminas.append({"tipo": "cierre", "titulo": "Guardalo para *tenerlo a mano*",
                        "texto": f"Y comentá *{palabra}* si querés la guía completa."})
        return c.editorial(laminas, usuario)
    fotos = list(fotos or [])
    for i, l in enumerate(laminas):
        if fotos:
            l["foto_arriba"] = fotos[(2 * i) % len(fotos)] if len(fotos) > 1 else None
            l["foto_abajo"] = fotos[(2 * i + 1) % len(fotos)]
    laminas.append({"tipo": "cierre", "texto": "El trading no es apostar. Es tener un *plan* y respetarlo.", "palabra": palabra})
    return c.partida(laminas, usuario)


def armar_html(contenido: dict, estilo: str, con_guia: bool, buscar=None, fotos_eze: list | None = None,
               usuario: str = "@ezebagnato", tema: str = "") -> list[bytes]:
    """El carrusel con el diseño nuevo. 'buscar(consulta) -> (jpg, es_logo, credito) | None' trae fotos reales."""
    import io as _io
    from . import carrusel_html as ch
    laminas = [dict(l) for l in contenido["laminas"]]
    palabra = contenido["palabra"]

    def traer(consulta):
        if not (buscar and consulta):
            return None
        try:
            return buscar(consulta)
        except Exception:
            return None

    if estilo == "partida":
        return ch.sacar_fotos(ch.partida(laminas, palabra, con_guia, list(fotos_eze or []), usuario))
    portada = next((l for l in laminas if l.get("tipo") == "portada"), None)
    foto, logo, credito = None, None, ""
    logo_img = logo_del_tema(tema) if tema else None
    if logo_img is not None:
        b = _io.BytesIO(); logo_img.save(b, "PNG"); logo = b.getvalue()
    elif portada is not None:
        r = traer(portada.get("foto") or tema)
        if r:
            foto, es_logo, credito = r
            if es_logo:
                logo, foto, credito = foto, None, ""
    if estilo == "editorial":
        return ch.sacar_fotos(ch.editorial(laminas, palabra, con_guia, usuario, foto=foto))
    puestas = 0
    for l in laminas:
        if l.get("tipo") == "paso" and l.get("foto") and puestas < 2:
            r = traer(l["foto"])
            if r and not r[1]:
                l["_foto"], l["_credito"] = r[0], r[2]
                puestas += 1
    return ch.sacar_fotos(ch.titular(laminas, palabra, con_guia, usuario, foto=foto, logo=logo, credito=credito))
