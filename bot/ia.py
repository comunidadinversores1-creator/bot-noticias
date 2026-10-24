"""El cerebro del bot: una IA que elige la noticia de más impacto, la redacta
y elige la foto. Funciona con Gemini (gratis), Groq (gratis), Claude u OpenAI:
usa la primera clave que encuentre y, si una falla, prueba con la siguiente."""
from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime

import requests

from .filtro import Grupo
from .util import log, ocultar, socios_activos, zona

URL_COMPATIBLE = {
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
    "groq": "https://api.groq.com/openai/v1",
    "openai": "https://api.openai.com/v1",
}
VARIABLE = {"anthropic": "ANTHROPIC_API_KEY", "gemini": "GEMINI_API_KEY", "groq": "GROQ_API_KEY", "openai": "OPENAI_API_KEY"}


class SinCerebro(Exception):
    """Ninguna IA respondió (o no hay ninguna clave cargada)."""


class Saltar(Exception):
    """Este modelo o proveedor no sirve ahora: probar el siguiente."""

    def __init__(self, motivo: str, proveedor_entero: bool = False) -> None:
        super().__init__(motivo)
        self.proveedor_entero = proveedor_entero


# ------------------------------------------------------------------ conexión

class Cerebro:
    def __init__(self, cfg: dict, entorno: dict | None = None) -> None:
        entorno = os.environ if entorno is None else entorno
        ia = cfg.get("ia", {})
        modelos = ia.get("modelos", {})
        self.proveedores: list[dict] = []
        for nombre in ia.get("orden", ["anthropic", "gemini", "groq", "openai"]):
            clave = (entorno.get(VARIABLE.get(nombre, "")) or "").strip()
            if not clave:
                continue
            lista = list(modelos.get(nombre) or [])
            base = URL_COMPATIBLE.get(nombre)
            if nombre == "openai":
                base = (entorno.get("OPENAI_BASE_URL") or base).rstrip("/")
                if entorno.get("OPENAI_MODEL"):
                    lista = [entorno["OPENAI_MODEL"]]
            if lista:
                self.proveedores.append({"nombre": nombre, "clave": clave, "base": base, "modelos": lista})
        self.llamadas = 0
        self.ultimo_error = ""
        self.sin_extras: set[tuple[str, str]] = set()
        self.caidos: set[tuple[str, str]] = set()      # modelos que ya fallaron en esta corrida
        # Tope de tiempo para toda la corrida: si la IA está lenta, mejor cortar que colgarse.
        self.vence = time.monotonic() + float(ia.get("segundos_maximos", 240))

    @property
    def disponible(self) -> bool:
        return bool(self.proveedores)

    # -- una llamada a un modelo concreto
    def _llamar(self, p: dict, modelo: str, sistema: str, usuario: str, max_tokens: int, temperatura: float) -> str:
        nombre, clave = p["nombre"], p["clave"]
        if nombre == "anthropic":
            url = "https://api.anthropic.com/v1/messages"
            cabeceras = {"x-api-key": clave, "anthropic-version": "2023-06-01", "content-type": "application/json"}
            cuerpos = [{
                "model": modelo, "max_tokens": max_tokens, "temperature": temperatura,
                "system": sistema, "messages": [{"role": "user", "content": usuario}],
            }]
        else:
            url = p["base"] + "/chat/completions"
            cabeceras = {"Authorization": f"Bearer {clave}", "Content-Type": "application/json"}
            base = {
                "model": modelo, "temperature": temperatura,
                "messages": [{"role": "system", "content": sistema}, {"role": "user", "content": usuario}],
            }
            completo = dict(base, max_tokens=max_tokens, response_format={"type": "json_object"})
            if nombre == "gemini":
                # Estos modelos "piensan" antes de contestar y eso gasta del mismo tope.
                completo["max_tokens"] = max_tokens + 3000
                completo["reasoning_effort"] = "low"
            # Si el servicio rechaza algún parámetro opcional, se reintenta con lo mínimo
            # (y se recuerda, para no chocar con el mismo rechazo en cada llamada).
            minimo = dict(base, max_tokens=max_tokens + 3000)
            cuerpos = [minimo] if (nombre, modelo) in self.sin_extras else [completo, minimo]

        ultimo = ""
        for cuerpo in cuerpos:
            parametro_rechazado = False
            for intento in range(2):
                if time.monotonic() > self.vence:
                    raise SinCerebro("Se acabó el tiempo reservado para la IA en esta corrida.")
                self.llamadas += 1
                try:
                    r = requests.post(url, headers=cabeceras, json=cuerpo, timeout=45)
                except requests.RequestException as error:
                    ultimo = type(error).__name__
                    time.sleep(2)
                    continue
                if r.status_code == 200:
                    return self._texto(nombre, r)
                ultimo = f"{r.status_code}: {ocultar(r.text[:300], clave)}"
                if r.status_code in (401, 403):
                    raise Saltar(f"clave rechazada ({ultimo})", proveedor_entero=True)
                if r.status_code == 429:
                    espera = _segundos(r.headers.get("retry-after"))
                    if intento == 0 and espera is not None and espera <= 20:
                        time.sleep(espera + 1)
                        continue
                    raise Saltar(f"límite de uso ({ultimo})")
                if r.status_code >= 500:
                    time.sleep(2)
                    continue
                parametro_rechazado = True     # 400/404/422: probar con el pedido mínimo
                if len(cuerpos) > 1 and cuerpo is cuerpos[0]:
                    self.sin_extras.add((nombre, modelo))
                break
            if not parametro_rechazado:
                break                          # caída o corte de red: pasar al modelo siguiente
        raise Saltar(ultimo or "sin respuesta")

    @staticmethod
    def _texto(nombre: str, r: requests.Response) -> str:
        try:
            datos = r.json()
            if nombre == "anthropic":
                texto = "".join(b.get("text", "") for b in datos.get("content", []) if b.get("type") == "text")
            else:
                texto = datos["choices"][0]["message"].get("content") or ""
        except (ValueError, KeyError, IndexError, TypeError, AttributeError) as error:
            raise Saltar(f"respuesta rara ({type(error).__name__})")
        if not texto.strip():
            raise Saltar("respuesta vacía")
        return texto

    def completar(self, sistema: str, usuario: str, max_tokens: int = 1200, temperatura: float = 0.3) -> str:
        if not self.proveedores:
            raise SinCerebro("No hay ninguna clave de IA cargada.")
        for p in self.proveedores:
            for modelo in p["modelos"]:
                if (p["nombre"], modelo) in self.caidos:
                    continue                 # ya falló hace un momento: no perder tiempo de nuevo
                try:
                    return self._llamar(p, modelo, sistema, usuario, max_tokens, temperatura)
                except Saltar as error:
                    self.ultimo_error = f"{p['nombre']}/{modelo}: {error}"
                    log.warning("  IA %s", self.ultimo_error)
                    self.caidos.add((p["nombre"], modelo))
                    if error.proveedor_entero:
                        self.caidos.update((p["nombre"], m) for m in p["modelos"])
                        break
        raise SinCerebro(f"Ninguna IA respondió. Último error: {self.ultimo_error}")

    def pedir_json(self, sistema: str, usuario: str, **opciones) -> dict:
        ultimo = ""
        for intento in range(2):
            texto = self.completar(sistema, usuario if intento == 0 else usuario + "\n\nRespondé SOLO con el JSON pedido.", **opciones)
            datos = extraer_json(texto)
            if isinstance(datos, dict):
                return datos
            ultimo = texto[:200]
        raise SinCerebro(f"La IA no devolvió un JSON válido: {ultimo!r}")


def _segundos(valor: str | None) -> float | None:
    try:
        return float(valor) if valor is not None else None
    except ValueError:
        return None


def extraer_json(texto: str):
    """Saca el JSON de la respuesta aunque venga con adornos (```json, <think>, texto antes o después)."""
    t = re.sub(r"(?is)<think>.*?</think>", "", texto or "").strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t).strip()
    try:
        return json.loads(t)
    except ValueError:
        pass
    inicio, fin = t.find("{"), t.rfind("}")
    if inicio != -1 and fin > inicio:
        try:
            return json.loads(t[inicio : fin + 1])
        except ValueError:
            return None
    return None


# ------------------------------------------------------------------ 1) elegir

SISTEMA_ELEGIR = """Sos el editor jefe de un canal de noticias financieras y cripto en español. Tu público son inversores minoristas de Argentina y Latinoamérica.

Te paso una lista de titulares recientes. Elegí SOLO los que de verdad pueden mover mercados o cambiarle las reglas del juego a un inversor. Ante la duda, no elijas: es mejor no publicar que publicar relleno.

SÍ es noticia de impacto:
- Decisiones o señales fuertes de bancos centrales (Fed, BCE, BCRA): tasas, emisión, intervenciones.
- Datos macro recién publicados (inflación, empleo, PBI), sobre todo si sorprenden.
- Leyes, decretos, regulaciones, fallos judiciales, sanciones y aranceles que afectan mercados, impuestos o cripto.
- Guerras, ataques y escaladas con efecto en petróleo, comercio o riesgo global; también treguas y acuerdos.
- Empresas: resultados de gigantes, quiebras, fusiones, hackeos, fraudes, salidas a bolsa grandes.
- Exchanges y brokers (cualquiera): hackeos y robos, retiros suspendidos, caídas del servicio, cierres, quiebras, multas o sanciones fuertes, compras y fusiones, e hitos históricos (récord de usuarios o de volumen, salida a bolsa, una licencia clave).
- MARCAS CON FOCO (SOCIOS): además de todo lo anterior, de estas marcas también interesan los productos nuevos y las campañas (por ejemplo una tarjeta, una herramienta nueva, una promoción para usuarios nuevos). A esas novedades poneles impacto MINIMO aunque no muevan el mercado.
- Movimientos fuertes de precio (acciones, bonos, dólar, petróleo, oro, Bitcoin) con causa clara.
- Argentina: dólar, reservas, riesgo país, deuda, FMI, medidas económicas, inflación del INDEC.

NO es noticia de impacto (no la elijas):
- Opinión, análisis, columnas, entrevistas, predicciones de precio y pronósticos de analistas.
- Guías, explicadores y notas de servicio (cobros, jubilaciones, trámites, cotizaciones de rutina).
- Promociones y lanzamientos menores de otras marcas (los de las MARCAS CON FOCO sí valen), notas patrocinadas, reseñas, comparativas y rumores sin confirmar.
- Lo que repite algo que YA PUBLICAMOS (lista abajo), salvo que traiga una novedad importante.

Si dos candidatos cuentan LA MISMA noticia (aunque uno esté en inglés y otro en español, o sean de medios distintos), elegí uno solo: el que traiga más detalle.

Escala de impacto (1 a 10):
10 = hecho histórico (guerra entre potencias, quiebra sistémica, cambio de régimen monetario).
9 = decisión de tasas, dato macro clave muy lejos de lo esperado, escalada bélica que sacude al petróleo, hackeo o quiebra gigante.
8 = ley o regulación importante aprobada, resultados de una empresa gigante, hackeo o cierre de un exchange o broker conocido, movimiento brusco de Bitcoin o de los índices, medida económica fuerte en Argentina.
7 = noticia relevante que un inversor tiene que conocer hoy.
6 = noticia interesante pero no imprescindible: solo sirve para un día con pocas noticias.
5 o menos = relleno, no vale la pena publicarla.

"urgente" es true solo si pasó recién y es de impacto URGENTE o más.

Respondé ÚNICAMENTE con JSON válido, sin texto extra, con esta forma:
{"elegidas":[{"id":"...","impacto":8,"categoria":"...","urgente":false,"motivo":"..."}]}
Como máximo 5 elegidas, ordenadas de mayor a menor impacto. Elegí solo las que lleguen a PISO o más; si ninguna llega, devolvé {"elegidas":[]}.
"categoria" tiene que ser una de: CATEGORIAS."""


def _si(valor) -> bool:
    """La IA puede contestar true, "true", "sí"... y "false" (que como texto NO es verdadero)."""
    return valor is True or str(valor).strip().lower() in ("true", "si", "sí", "1", "yes")


def _hace(momento: datetime, ahora: datetime) -> str:
    minutos = max(0, int((ahora - momento).total_seconds() // 60))
    return f"hace {minutos} min" if minutos < 90 else f"hace {minutos // 60} h"


def elegir(cerebro: Cerebro, candidatos: list[Grupo], ya_publicadas: list[str], cfg: dict, ahora: datetime) -> list[Grupo]:
    """Le muestra los candidatos a la IA y devuelve los elegidos con su impacto."""
    if not candidatos:
        return []
    categorias = [str(c) for c in cfg["categorias"]]
    lineas = []
    for g in candidatos:
        medios = ", ".join(g.fuentes[:4])
        resumen = f" — {g.principal.resumen[:150]}" if g.principal.resumen else ""
        lineas.append(f"id={g.id} | {_hace(g.fecha, ahora)} | {g.cobertura} medio(s): {medios} | {g.principal.titulo[:170]}{resumen}")
    local = ahora.astimezone(zona(cfg)).strftime("%d/%m/%Y %H:%M")
    publicadas = "\n".join(f"- {t}" for t in ya_publicadas) or "- (nada todavía)"
    usuario = f"Fecha y hora (Argentina): {local}\n\nYA PUBLICAMOS (no repetir):\n{publicadas}\n\nTITULARES CANDIDATOS:\n" + "\n".join(lineas)
    socios = ", ".join(str(s.get("nombre", k)) for k, s in socios_activos(cfg).items()) or "ninguna"
    r = cfg["ritmo"]
    minimo = int(r.get("impacto_minimo", 7))
    piso = min(minimo, int(r.get("impacto_de_relleno") or minimo))
    urgente = int(r.get("impacto_urgente", 9))
    sistema = (
        SISTEMA_ELEGIR.replace("CATEGORIAS", ", ".join(categorias)).replace("SOCIOS", socios)
        .replace("PISO", str(piso)).replace("MINIMO", str(minimo)).replace("URGENTE", str(urgente))
    )
    datos = cerebro.pedir_json(sistema, usuario, max_tokens=700, temperatura=0.1)

    # La IA a veces devuelve un solo objeto en vez de una lista, o cualquier otra cosa: nada de eso frena la corrida.
    elegidas = datos.get("elegidas")
    if isinstance(elegidas, dict):
        elegidas = [elegidas]
    if not isinstance(elegidas, list):
        elegidas = []

    por_id = {g.id: g for g in candidatos}
    elegidos: list[Grupo] = []
    for item in elegidas[:5]:
        if not isinstance(item, dict):
            continue
        g = por_id.get(str(item.get("id", "")).strip())
        if g is None or g in elegidos:
            continue
        try:
            g.impacto = max(1, min(10, int(float(item.get("impacto", 0)))))
        except (TypeError, ValueError, OverflowError):
            continue
        categoria = str(item.get("categoria", "")).strip().upper()
        g.categoria = categoria if categoria in categorias else (g.principal.tema or categorias[0])
        # "Última hora" solo si es muy fuerte Y salió hace menos de una hora y media.
        reciente = (ahora - g.fecha).total_seconds() <= 90 * 60
        g.urgente = _si(item.get("urgente")) and g.impacto >= urgente and reciente
        log.info("  IA eligió [%d] %s: %s (%s)", g.impacto, g.categoria, g.principal.titulo[:80], str(item.get("motivo", ""))[:90])
        elegidos.append(g)
    return elegidos


# ---------------------------------------------------------------- 2) redactar

SISTEMA_REDACTAR = """Sos redactor de un canal de noticias financieras y cripto para inversores de Argentina y Latinoamérica. Escribís en español rioplatense: claro, directo, con datos concretos y sin vueltas. Lo tiene que entender alguien que recién empieza. Decí "dinero", no "plata" (salvo que hables del metal).

REGLAS QUE NO SE ROMPEN:
1. Usá SOLO los hechos que están en el TEXTO ORIGINAL. No agregues datos, nombres, fechas ni cifras que no estén ahí. Si el texto original solo trae el titular, el resumen cuenta ese hecho en una o dos oraciones y nada más.
2. Las cifras se copian con los mismos dígitos del original (podés cambiar el punto decimal por coma). No conviertas unidades ni redondees. Ojo: "billion" en inglés es "mil millones" (1.5 billion = 1,5 mil millones) y "trillion" es "billón".
3. Nada de consejos de inversión, predicciones de precio ni promesas de ganancia. Contás lo que pasó.
4. "por_que_importa" explica el mecanismo en general y en potencial ("puede", "suele"), SIN ninguna cifra.
5. Sin links, sin hashtags, sin arrobas, sin nombres de páginas web.
6. Tono serio. Si es un conflicto armado o una tragedia, sin sensacionalismo; y si hay muertos o heridos, "pregunta" va vacía ("").
7. Todo en español aunque el original esté en inglés. Los nombres propios no se traducen.

Respondé ÚNICAMENTE con JSON válido, sin texto extra:
{
 "titular": "el hecho en una línea, máx. 85 caracteres, sin emojis y sin punto final",
 "titular_imagen": "versión más corta para la placa, máx. 58 caracteres, sin emojis",
 "resumen": "2 o 3 oraciones, máx. 330 caracteres",
 "dato_clave": "el dato más importante en una oración, máx. 110 caracteres",
 "por_que_importa": "una oración, máx. 150 caracteres, sin cifras",
 "pregunta": "pregunta corta, con voseo, para que la comunidad opine sobre el hecho (nunca sobre comprar o vender), máx. 70 caracteres. Variá la forma: a veces una pregunta directa, a veces pedí que voten con ✅ o ❌",
 "bandera": "emoji de la bandera del país protagonista, o \\"\\" si no aplica",
 "tono": "alcista, bajista o neutral (cómo cae la noticia en los mercados)",
 "fuente": "nombre del medio principal, sin .com",
 "fotos": ["3 búsquedas EN INGLÉS para encontrar una FOTO REAL en Wikimedia Commons, de la más precisa a la más general. Primero la persona, institución, empresa o lugar protagonista (ej.: Jerome Powell, Federal Reserve building, Strait of Hormuz). Si la protagonista es una empresa, un exchange, un broker o una criptomoneda, la PRIMERA búsqueda es su logo (ej.: Binance logo, Nvidia logo, Bitcoin logo). La última puede ser un objeto o escena real (ej.: oil tanker ship)."],
 "escena": "la escena para la imagen de la noticia, en español: los lugares, objetos y símbolos que la cuentan (ej.: el Capitolio de Illinois al atardecer, la bandera del estado, monedas doradas de bitcoin y ethereum, un martillo de juez y un gráfico en rojo de fondo). Sin personas reales, sin logos de marcas y sin texto. Máx. 220 caracteres",
 "para_la_marca": "solo si arriba dice MARCA CON FOCO; si no, \\"\\". Cómo le cae la noticia a ESA marca: buena (un producto o función nueva, una campaña, una promoción, un logro, un récord, una licencia obtenida, una expansión), mala (hackeo, multa, demanda, investigación, arresto, caída del servicio, retiros frenados, cierre, salida de un país, despidos, quejas, pérdida de licencia) o neutra (todo lo demás, o si la marca aparece de pasada). Ante la duda, neutra",
 "llamado": "true o false. Solo puede ser true si \\"para_la_marca\\" es buena y tiene sentido invitar a registrarse; en cualquier otro caso, false"
}"""


def redactar(cerebro: Cerebro, g: Grupo, correccion: str = "", marca_con_foco: str = "") -> dict:
    usuario = f"CATEGORÍA: {g.categoria}\nMEDIOS: {', '.join(g.fuentes[:5])}\n"
    if marca_con_foco:
        usuario += f"MARCA CON FOCO: {marca_con_foco}\n"
    usuario += f"\nTEXTO ORIGINAL:\n{g.texto_fuente()}"
    if correccion:
        usuario += f"\n\nATENCIÓN: {correccion}"
    return cerebro.pedir_json(SISTEMA_REDACTAR, usuario, max_tokens=900, temperatura=0.4)


# ------------------------------------------------------------------ promos

SISTEMA_PROMO = """Escribís publicaciones de venta para las redes de Ezequiel Bagnato (Comunidad Inversores): trading, cripto y herramientas propias. Español rioplatense, con voseo, directo y honesto. Tiene que sonar a una persona real recomendando algo que usa, no a un aviso.

REGLAS QUE NO SE ROMPEN:
1. Usá SOLO lo que dicen los DATOS. No inventes características, precios, bonos, plazos ni resultados.
2. Prohibido prometer o insinuar ganancias, y prohibido dar cifras de resultados pasados (cuánto ganó, cuánto rindió, en cuánto se convirtió el dinero). No uses estas palabras salvo para advertir ("no garantiza ganancias"): ganancia, ganar, rendimiento, rentable, rentabilidad, retorno, asegurado, garantizado, sin riesgo, ingresos pasivos, dinero fácil, duplicar. Sí podés decir que hay un backtest, un dossier o un video que muestra los resultados y los riesgos. Un porcentaje solo vale si es un dato del producto que figura en los DATOS (un descuento, un reintegro).
3. Sin cifras que no figuren en los DATOS.
4. Sin links, sin hashtags, sin arrobas: el link lo agrega el sistema.
5. No nombres a otras empresas ni compares con competidores.
6. Seguí el ENFOQUE que se te indica, así cada publicación sale distinta de la anterior.

Respondé ÚNICAMENTE con JSON válido, sin texto extra:
{
 "titular": "gancho de una línea, máx. 70 caracteres, sin emojis y sin punto final",
 "titular_imagen": "versión para la placa, máx. 44 caracteres, sin emojis",
 "texto": "2 o 3 oraciones, máx. 240 caracteres",
 "puntos": ["3 beneficios o características concretas, cada uno de máx. 36 caracteres, sin punto final"]
}"""

ENFOQUES = [
    "Qué problema le resuelve a la persona.",
    "Cómo funciona, explicado bien simple.",
    "Para quién es y para quién NO es.",
    "El error más común que ayuda a evitar.",
    "Los primeros pasos para empezar.",
    "La diferencia con hacerlo todo a mano.",
    "Una pregunta directa que haga pensar a la comunidad.",
    "Con total transparencia: qué hace y qué riesgos tiene.",
]


def redactar_promo(cerebro: Cerebro, promo: dict, marca: str, numero: int, correccion: str = "") -> dict:
    """Texto de venta de una herramienta o campaña, con un enfoque distinto cada vez."""
    enfoque = ENFOQUES[numero % len(ENFOQUES)]
    usuario = f"PRODUCTO: {promo.get('nombre', '')}\nMARCA: {marca}\nENFOQUE: {enfoque}\n\nDATOS:\n{str(promo.get('datos', '')).strip()}"
    if correccion:
        usuario += f"\n\nATENCIÓN: {correccion}"
    return cerebro.pedir_json(SISTEMA_PROMO, usuario, max_tokens=700, temperatura=0.7)


SISTEMA_JUEZ = """Sos revisor de publicidad de productos financieros. Te paso el texto de una publicación de venta y los DATOS del producto. Tu única tarea es decidir si el texto PROMETE o INSINÚA alguna de estas cosas:
- que la persona va a ganar dinero, o cuánto (ganancias, rendimientos, ingresos, resultados, que el capital crece);
- que no hay riesgo, que no se puede perder o que el resultado está asegurado;
- resultados pasados con cifras usados como anzuelo (cuánto ganó, cuánto rindió, en cuánto se convirtió el dinero).

NO es una promesa (respondé false):
- contar qué es la herramienta y cómo funciona (que opera sola, que es automática, que el dinero queda en la cuenta de cada uno);
- decir que existe un backtest, un dossier o un video que muestra los resultados y los riesgos, sin dar cifras de ganancia;
- invitar a un evento o avisar de un código de descuento;
- un beneficio concreto del producto que figure en los DATOS: un reintegro o descuento con su porcentaje, un bono de bienvenida, que algo es gratis;
- una advertencia honesta ("no garantiza ganancias", "podés perder").

Ante la duda, respondé true: es preferible frenar una publicación buena que dejar pasar una promesa.

Respondé ÚNICAMENTE con JSON: {"promete": true o false, "frase": "la frase problemática, copiada tal cual, o vacío"}"""


def juzgar_promo(cerebro: Cerebro, texto: str, datos: str) -> tuple[bool, str]:
    """Segunda revisión de una promo, hecha por la IA con una sola consigna: ¿promete ganancias?
    Devuelve (promete, frase). Si la respuesta no es un 'false' clarísimo, se toma como que promete."""
    respuesta = cerebro.pedir_json(SISTEMA_JUEZ, f"DATOS:\n{datos.strip()}\n\nTEXTO A REVISAR:\n{texto.strip()}", max_tokens=200, temperatura=0.0)
    promete = respuesta.get("promete")
    frase = respuesta.get("frase")
    limpio = promete is False or str(promete).strip().lower() in ("false", "no", "0")
    return (not limpio), (frase.strip()[:120] if isinstance(frase, str) else "")


# ------------------------------------------------------------ 3) elegir foto

SISTEMA_FOTO = """Elegís la imagen que acompaña una noticia. Te paso el titular y una lista numerada de imágenes reales: fotos (nombre de archivo, descripción y año) y logos oficiales.

Elegí la que muestre con más precisión al protagonista de la noticia (persona, institución, empresa o lugar) y que se vea actual y seria. Si la protagonista es una empresa, un exchange, un broker o una criptomoneda y está su logo oficial, elegí el logo.
Descartá: fotos sin relación directa, retratos viejos de alguien que hoy se ve distinto, dibujos, caricaturas, pinturas, mapas, capturas de pantalla, documentos, estampillas y fotos de otra persona con el mismo nombre.
Si ninguna sirve, respondé -1. Es preferible no poner foto que poner una equivocada.

Respondé ÚNICAMENTE con JSON: {"elegida": número}"""


def elegir_foto(cerebro: Cerebro, titular: str, opciones: list[str]) -> int | None:
    """Devuelve el índice de la foto elegida, -1 si ninguna sirve, o None si la IA no contestó."""
    lista = "\n".join(f"{i}. {o}" for i, o in enumerate(opciones))
    try:
        datos = cerebro.pedir_json(SISTEMA_FOTO, f"TITULAR: {titular}\n\nFOTOS:\n{lista}", max_tokens=200, temperatura=0.0)
        indice = int(datos.get("elegida"))
    except (SinCerebro, TypeError, ValueError):
        return None
    return indice if -1 <= indice < len(opciones) else None


# ------------------------------------------------- 4) charla en X (preguntas y dilemas)

SISTEMA_CHARLA = """Escribís publicaciones cortas para la cuenta de X de Ezequiel Bagnato (Comunidad Inversores). El objetivo es que la gente responda, opine y comparta. Temas: bots de trading, inteligencia artificial, trading, gestión del riesgo, psicología del trader, criptomonedas y exchanges, empresas tecnológicas, guerras y conflictos que mueven los mercados, y dinero del día a día (ahorro, sueldo, inversiones, bitcoin, dólar, jubilación).

Si te paso un TEMA DE HOY EN X con su CONTEXTO, la publicación es sobre eso (es de lo que la gente está hablando ahora): usá solo los hechos del contexto, sin inventar cifras ni detalles. Nombrá el tema con todas las letras: las personas, las empresas, las monedas y las causas que están en el contexto (ej.: "la causa $LIBRA", "Milei"), porque es lo que la gente busca y comenta. Si hay una causa judicial o una acusación, hablá en potencial o atribuí ("la presunta estafa", "según la causa", "habría", "el juez lo sobreseyó"): nunca afirmes que alguien cometió un delito. Se puede preguntar la opinión de la gente sobre el tema o el fallo. Si te pido HASHTAGS, devolvé en "hashtags" 1 o 2 de los que se usan para ese tema (nombres propios o del tema, ej. "Libra", "Milei"), nunca uno que acuse (nada de "estafa", "fraude", "chorro", "corrupto").

ESTILO (copialo bien):
- Español rioplatense con voseo, como habla un pibe argentino en X. Arrancá en minúscula. Frases cortas.
- Cada idea en su renglón, con un renglón en blanco entre medio.
- Sin emojis, sin hashtags, sin links, sin @menciones, sin comillas de más.
- Largo: máximo 240 caracteres, salvo el formato "largo".
- Decí siempre "dinero", nunca "plata".
- Cuando haya montos, escribilos con símbolo y número concreto: $3.000.000, $800.000, 2.000 usd, $15.000 por mes. Los números concretos son los que hacen que la gente opine.
- Nada de "hilo", "RT", "seguime" ni pedidos de like: solo el contenido.

FORMATOS (usá el que te pido):
- dilema: una elección incómoda con montos concretos en $ o usd, y al final una pregunta corta ("¿aceptás?", "¿qué elegís?"). Ej.: "te pagan $1.500 usd por mes pero solo laburás 4 horas por día\n\no $4.000 usd pero 10 horas por día\n\nqué elegís?"
- pregunta: una sola pregunta directa que cualquiera pueda contestar con su experiencia.
- caso: un caso INVENTADO contado como hipótesis ("imaginá un trader que...", "pensá en alguien que..."), 3 a 5 renglones, y una última línea que deja pensando.
- cuentas: un mini presupuesto o cálculo de ejemplo, un renglón por línea y SIN renglón en blanco entre ellas ("Sueldo: $3.000.000", "Alquiler: $800.000"...), 6 a 12 líneas, y al final una conclusión corta que pega. Puede pasar los 240 caracteres (hasta 500).
- largo: una reflexión o explicación de 500 a 1.200 caracteres sobre trading, riesgo, bots o dinero: arranca con una frase gancho, después desarrolla en renglones cortos (puede tener una lista de pasos o errores) y cierra con una frase que queda o una pregunta.
- ensenanza: explicá UN tema concreto para alguien que recién empieza (el TEMA viene como pregunta, tal como la busca la gente en Google). Primera línea: la pregunta, tal cual. Después la respuesta en renglones cortos y simples, de 400 a 1.000 caracteres: qué es, para qué sirve, un ejemplo, y un error común o un cuidado a tener. Si es un paso a paso de una app (Exness, Binance), explicá el camino en general y no inventes nombres exactos de botones, montos mínimos ni comisiones: si no estás seguro de un detalle, no lo pongas. Si te paso DATOS, usá solo esos datos. Cerrá con una pregunta corta para que comenten.
- venta: una pregunta que haga pensar en el problema que resuelve el PRODUCTO que te paso, sin nombrarlo como aviso y sin prometer resultados (ej.: "¿cuánto tiempo pasás mirando gráficos para encontrar una entrada?"). Corta, como las otras.
- encuesta: una pregunta corta que divida opiniones (máximo 200 caracteres) y entre 2 y 4 opciones de respuesta de hasta 25 caracteres cada una, sin emojis. Las opciones van en "opciones". Ej.: "bitcoin cae 10% mañana. ¿qué hacés?" con opciones "compro más", "vendo todo", "no toco nada", "ni me entero".
- lista: un post para guardar. Primera línea: un título gancho con número ("5 errores que te vacían la cuenta", "checklist antes de abrir una operación"). Después de 5 a 7 puntos numerados, cortos y concretos, uno por renglón. Última línea: "guardalo para tenerlo a mano". Entre 400 y 900 caracteres.
- opinion: una opinión fuerte pero defendible sobre trading, bots, IA o dinero, y una pregunta para que discutan.

REGLAS QUE NO SE ROMPEN:
- Nunca cuentes algo como real: prohibido "conocí", "hablé con", "me contó", "un amigo", "un alumno", "un seguidor". Los casos son hipótesis y se nota.
- Nunca prometas ganancias ni digas que algo rinde seguro, que no tiene riesgo o que alguien "vive de rendimientos". Ningún porcentaje de ganancia como resultado.
- Nunca recomiendes comprar o vender un activo concreto.
- Nada de política partidaria ni personas reales.
- No repitas ideas de las publicaciones anteriores que te paso.

Respondé SOLO con JSON: {"texto": "..."} (en el formato encuesta: {"texto": "...", "opciones": ["...", "..."]}; con tema de hoy en X, también "hashtags": ["...", "..."])"""

FORMATOS_CHARLA = ["dilema", "lista", "venta", "cuentas", "pregunta", "caso", "dilema", "lista", "venta", "opinion", "largo", "cuentas"]

TEMAS_CHARLA = [
    "bots de trading", "inteligencia artificial para invertir", "gestión del riesgo", "stop loss", "apalancamiento",
    "psicología del trader", "rachas de pérdidas", "backtesting", "disciplina", "operar noticias",
    "ahorro", "sueldo", "inversiones a largo plazo", "bitcoin", "dólar", "jubilación", "deudas", "gastos hormiga",
    "interés compuesto", "fondo de emergencia", "inteligencia artificial y empleo", "empresas tecnológicas",
    "exchanges de cripto", "guerras y mercados", "petróleo", "aranceles y comercio",
]


def redactar_charla(cerebro: Cerebro, formato: str, tema: str, anteriores: list[str], correccion: str = "", datos: str = "",
                    ganchos: list[str] | None = None) -> dict:
    if datos.startswith("CONTEXTO:"):
        usuario = f"FORMATO: {formato}\nTEMA DE HOY EN X: {tema}\n{datos.strip()}\n"
    else:
        usuario = f"FORMATO: {formato}\n{'PRODUCTO' if formato == 'venta' else 'TEMA'}: {tema}\n"
        if datos:
            usuario += f"DATOS:\n{datos.strip()}\n"
    if ganchos:
        # Lo que ya funciona: se reinventa, no se inventa de cero (y nunca se copia palabra por palabra).
        usuario += ("\nGANCHOS QUE ESTÁN PEGANDO ESTOS DÍAS EN X (tomá la ESTRUCTURA de uno que encaje con el tema y adaptala "
                    "a la voz de Eze y en español rioplatense; nunca copies las frases):\n- " + "\n- ".join(ganchos[:6]) + "\n")
    usuario += "\nPUBLICACIONES ANTERIORES (no repetir):\n" + (
        "\n---\n".join(a[:200] for a in anteriores[-25:]) or "(ninguna)")
    if correccion:
        usuario += f"\n\nATENCIÓN: {correccion}"
    return cerebro.pedir_json(SISTEMA_CHARLA, usuario, max_tokens=1500, temperatura=0.9)   # los posts largos (Premium) necesitan lugar


# ------------------------------------------------- 5) hilo semanal en X

SISTEMA_HILO = """Armás el hilo semanal de X de Ezequiel Bagnato (Comunidad Inversores): "lo que movió el mercado esta semana". Te paso las noticias que se publicaron en la semana (título y categoría).

- Elegí las CANTIDAD más importantes para alguien que invierte o hace trading, de temas distintos.
- "gancho": el primer post, que haga querer leer el hilo (máximo 200 caracteres), sin emojis al principio. Ej.: "la semana tuvo de todo: petróleo arriba de 114 dólares, riesgo país récord y un ETF nuevo de bitcoin.\n\nlo que movió el mercado, en 5 posts 👇"
- "posts": uno por noticia elegida, numerados ("1/", "2/"...), cada uno de hasta 260 caracteres: qué pasó y por qué importa, en criollo.
- "cierre": una pregunta para que comenten y la invitación a seguir la cuenta para no perderse el de la semana que viene.
- Usá SOLO los hechos y cifras que están en los títulos. No inventes números ni detalles.
- Nombrá con todas las letras a las personas, empresas, monedas y causas de las noticias: es lo que la gente busca. Si hay una acusación o una causa judicial, en potencial o atribuido ("la presunta estafa", "según la causa"): nunca afirmes que alguien cometió un delito.
- Español rioplatense con voseo. Decí siempre "dinero", nunca "plata". Sin links ni @menciones adentro de los posts.
- "hashtags": 1 o 2 de los temas más fuertes de la semana (nombres propios o temas, sin #), nunca uno que acuse.

Respondé SOLO con JSON: {"gancho": "...", "posts": ["1/ ...", "2/ ..."], "cierre": "...", "hashtags": ["...", "..."]}"""


def redactar_hilo(cerebro: Cerebro, noticias: list[dict], cantidad: int = 5, correccion: str = "") -> dict:
    lista = "\n".join(f"- [{n.get('categoria', '')}] {n.get('titular', '')}" for n in noticias)
    usuario = f"CANTIDAD: {cantidad}\n\nNOTICIAS DE LA SEMANA:\n{lista}"
    if correccion:
        usuario += f"\n\nATENCIÓN: {correccion}"
    return cerebro.pedir_json(SISTEMA_HILO.replace("CANTIDAD", str(cantidad)), usuario, max_tokens=1500, temperatura=0.5)


# ------------------------------------------------- 6) material que manda Eze (post: / hilo:)

SISTEMA_MATERIAL = """Escribís para la cuenta de X de Ezequiel Bagnato (trader, bots de trading, IA, cripto). Eze te manda notas (y a veces una captura) y vos armás el post o el hilo con SUS datos.

- Usá solo los hechos y números de sus notas: no inventes cifras ni resultados.
- Español rioplatense con voseo, en minúscula, frases cortas, renglón en blanco entre ideas. Decí siempre "dinero", nunca "plata".
- Si son resultados de un bot o de una operación, mostrá lo bueno y lo malo, sin prometer que se va a repetir ni decir que no hay riesgo.
- Primera línea: un gancho que haga frenar el scroll (un número, una pregunta, una advertencia).
- Sin links ni @menciones. Podés sumar 1 o 2 hashtags al final si tienen que ver.
- TIPO post: un solo post de hasta 900 caracteres. TIPO hilo: "partes" con un gancho, de 3 a 6 posts numerados ("1/") de hasta 270 caracteres cada uno y un cierre con una pregunta.

Respondé SOLO con JSON: {"partes": ["...", "..."]} (en un post, una sola parte)"""


def redactar_material(cerebro: Cerebro, tipo: str, notas: str, con_foto: bool, correccion: str = "") -> dict:
    usuario = f"TIPO: {tipo}\n{'(Eze mandó una captura que va en el primer post.)' if con_foto else ''}\n\nNOTAS DE EZE:\n{notas.strip()}"
    if correccion:
        usuario += f"\n\nATENCIÓN: {correccion}"
    return cerebro.pedir_json(SISTEMA_MATERIAL, usuario, max_tokens=1500, temperatura=0.6)
