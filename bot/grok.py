"""Grok (xAI): la única IA que lee X en tiempo real. Se usa para dos cosas:
1) saber de qué se está hablando HOY en X, para que las preguntas salgan sobre temas calientes;
2) encontrar posts virales del nicho, para que Eze les responda a mano (responder automático
   a otras personas está prohibido por las reglas de X).

Si no está la llave XAI_API_KEY, el bot sigue como siempre (sin estas dos cosas)."""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta

import requests

from .ia import extraer_json
from .util import log

URL = "https://api.x.ai/v1/responses"

AREAS = ("trading, criptomonedas, exchanges, inteligencia artificial, empresas tecnológicas, bolsa, "
         "economía, guerras y conflictos que mueven los mercados, dólar, dinero y ahorro")


class SinGrok(Exception):
    pass


def disponible(entorno: dict | None = None) -> bool:
    return bool(((entorno or os.environ).get("XAI_API_KEY") or "").strip())


def _texto_de(respuesta: dict) -> str:
    """Saca el texto final de una respuesta de la API (formato 'responses')."""
    if isinstance(respuesta.get("output_text"), str) and respuesta["output_text"].strip():
        return respuesta["output_text"]
    partes = []
    for item in respuesta.get("output") or []:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for c in item.get("content") or []:
            if isinstance(c, dict) and isinstance(c.get("text"), str):
                partes.append(c["text"])
    return "\n".join(partes)


def preguntar(cfg: dict, consigna: str, desde: datetime, max_turnos: int = 3) -> dict:
    clave = (os.environ.get("XAI_API_KEY") or "").strip()
    if not clave:
        raise SinGrok("falta XAI_API_KEY")
    g = cfg.get("grok") or {}
    cuerpo = {
        "model": str(g.get("modelo") or "grok-4.7"),
        "input": [{"role": "user", "content": consigna}],
        "tools": [{"type": "x_search", "from_date": desde.strftime("%Y-%m-%d")}],
        "max_turns": max_turnos,
    }
    try:
        r = requests.post(URL, headers={"Authorization": f"Bearer {clave}", "Content-Type": "application/json"},
                          json=cuerpo, timeout=(10, 120))
    except requests.RequestException as error:
        raise SinGrok(f"no respondió ({type(error).__name__})")
    if r.status_code == 401:
        raise SinGrok("rechazó la llave (XAI_API_KEY)")
    if r.status_code == 402 or (r.status_code == 403 and "credit" in r.text.lower()):
        raise SinGrok("no tiene saldo cargado")
    if r.status_code != 200:
        raise SinGrok(f"respondió con error {r.status_code}")
    try:
        datos = extraer_json(_texto_de(r.json()))
    except ValueError:
        datos = None
    if not isinstance(datos, dict):
        raise SinGrok("no devolvió el formato pedido")
    return datos


def temas_del_dia(cfg: dict, momento: datetime) -> list[dict]:
    """Los temas de los que más se está hablando ahora en X (en español), dentro del nicho."""
    areas = str((cfg.get("grok") or {}).get("areas") or AREAS)
    consigna = (
        f"Buscá en X qué se está hablando HOY (últimas 24 horas) en español, sobre: {areas}. "
        "Quiero los 6 temas con más conversación y que muevan o puedan mover los mercados o el dinero de la gente. "
        "Nada de fútbol ni farándula. En el contexto nombrá a las personas, empresas y causas involucradas, con hechos verificables y en potencial si hay acusaciones. "
        "Respondé SOLO con JSON: {\"temas\": [{\"tema\": \"en 5 a 10 palabras\", \"contexto\": \"qué pasó, en 1 o 2 oraciones, solo hechos\", "
        "\"hashtag\": \"el hashtag que más se usa para ese tema, sin #, o vacío\", "
        "\"cuentas\": [\"usuarios oficiales de X de las personas o empresas involucradas, sin @, solo si estás seguro\"]}]}"
    )
    datos = preguntar(cfg, consigna, momento - timedelta(days=1))
    temas = []
    for t in datos.get("temas") or []:
        if isinstance(t, dict) and str(t.get("tema") or "").strip():
            item = {k: str(t.get(k) or "").strip()[:300] for k in ("tema", "contexto", "hashtag")}
            item["cuentas"] = [re.sub(r"[^\w]", "", str(c)) for c in (t.get("cuentas") or []) if re.sub(r"[^\w]", "", str(c))][:3]
            temas.append(item)
    return temas[:8]


def virales(cfg: dict, momento: datetime, cantidad: int = 5) -> list[dict]:
    """Posts que están explotando en el nicho, para que Eze les responda a mano."""
    areas = str((cfg.get("grok") or {}).get("areas") or AREAS)
    consigna = (
        f"Buscá en X los {cantidad} posts en español de las últimas 24 horas con MÁS interacción (respuestas, reposts, likes) sobre: {areas}. "
        "Que sean de cuentas grandes o de posts que se están haciendo virales, donde una buena respuesta pueda ganar visibilidad. "
        "Nada de cuentas de estafas, sorteos o spam. "
        "Para cada uno, proponé una idea de respuesta corta que aporte (un dato, una experiencia o una pregunta), en español rioplatense, "
        "sin promocionar nada y diciendo 'dinero', nunca 'plata'. "
        "Respondé SOLO con JSON: {\"posts\": [{\"url\": \"https://x.com/...\", \"cuenta\": \"@usuario\", \"de_que_habla\": \"1 oración\", "
        "\"por_que\": \"por qué conviene responder (ej.: 300 respuestas en 2 horas)\", \"idea\": \"la respuesta sugerida\"}]}"
    )
    datos = preguntar(cfg, consigna, momento - timedelta(days=1), max_turnos=4)
    posts = []
    for p in datos.get("posts") or []:
        if not isinstance(p, dict):
            continue
        url = str(p.get("url") or "").strip()
        if not (url.startswith("https://x.com/") or url.startswith("https://twitter.com/")):
            continue
        posts.append({k: str(p.get(k) or "").strip()[:400] for k in ("url", "cuenta", "de_que_habla", "por_que", "idea")})
    return posts[:cantidad]


def guardar_json(datos) -> str:
    return json.dumps(datos, ensure_ascii=False)


def para_citar(cfg: dict, momento: datetime, titular: str, resumen: str) -> dict | None:
    """El post de X más importante sobre una noticia grande (de un medio o una cuenta conocida), para que
    Eze lo cite con su opinión."""
    consigna = (
        f"Esta noticia acaba de salir: \"{titular}\". {resumen}\n"
        "Buscá en X el post sobre esta noticia que más interacción tiene en este momento, de un medio, un periodista o una cuenta "
        "reconocida de finanzas o cripto (en español o en inglés). Proponé una cita corta (máximo 200 caracteres) con la mirada "
        "de un trader: qué significa para el mercado o qué conviene mirar, sin predecir precios ni recomendar comprar o vender, "
        "en español rioplatense y diciendo 'dinero', nunca 'plata'. "
        "Respondé SOLO con JSON: {\"url\": \"https://x.com/...\", \"cuenta\": \"@usuario\", \"cita\": \"...\"}"
    )
    datos = preguntar(cfg, consigna, momento - timedelta(days=1))
    url = str(datos.get("url") or "").strip()
    if not (url.startswith("https://x.com/") or url.startswith("https://twitter.com/")):
        return None
    return {"url": url, "cuenta": str(datos.get("cuenta") or "").strip()[:60], "cita": str(datos.get("cita") or "").strip()[:300]}


def inspiracion(cfg: dict, momento: datetime, cantidad: int = 2) -> list[dict]:
    """Posts que se hicieron virales por su FORMATO (no por quién los escribió), con un borrador propio
    de Eze que toma la idea y la estructura, pero con su tema y su voz: nunca una copia."""
    areas = str((cfg.get("grok") or {}).get("areas") or AREAS)
    consigna = (
        f"Buscá en X {cantidad} posts en español de los últimos 3 días sobre {areas} que se hicieron virales por cómo están escritos "
        "(una pregunta, un dilema, una lista, una cuenta con números, una opinión polémica), de cuentas chicas o medianas: "
        "que el éxito venga del formato y no de la fama de la cuenta. Nada de estafas, sorteos ni promesas de ganancias. "
        "Para cada uno escribí un borrador ORIGINAL para la cuenta de Ezequiel Bagnato (trader, bots de trading, IA, cripto): "
        "que use la misma estructura que lo hizo viral pero con otro tema o ángulo propio, sin copiar frases. Reglas del borrador: "
        "español rioplatense, en minúscula, frases cortas con renglón en blanco entre ideas, sin emojis ni hashtags ni links, "
        "decir 'dinero' y nunca 'plata', no prometer ganancias, no contar casos inventados como reales (nada de 'conocí a un pibe'). "
        "Respondé SOLO con JSON: {\"posts\": [{\"url\": \"https://x.com/...\", \"cuenta\": \"@usuario\", "
        "\"por_que_funciono\": \"1 oración sobre el formato\", \"borrador\": \"el post para Ezequiel\"}]}"
    )
    datos = preguntar(cfg, consigna, momento - timedelta(days=3), max_turnos=4)
    salida = []
    for p in datos.get("posts") or []:
        if not isinstance(p, dict):
            continue
        url = str(p.get("url") or "").strip()
        borrador = str(p.get("borrador") or "").strip()
        if not (url.startswith("https://x.com/") or url.startswith("https://twitter.com/")) or not borrador:
            continue
        salida.append({"url": url, "cuenta": str(p.get("cuenta") or "").strip()[:60],
                       "por_que_funciono": str(p.get("por_que_funciono") or "").strip()[:300], "borrador": borrador[:1200]})
    return salida[:cantidad]


def estrategias(cfg: dict, momento: datetime) -> dict:
    """Informe semanal: qué hacen en X las cuentas del nicho que más crecen (en español y en inglés)."""
    areas = str((cfg.get("grok") or {}).get("areas") or AREAS)
    consigna = (
        f"Analizá en X las cuentas de {areas} (en español y en inglés) que más engagement tuvieron en los últimos 7 días, "
        "sobre todo cuentas chicas y medianas que están creciendo rápido. No me interesan los posts sueltos sino las ESTRATEGIAS "
        "que repiten: formatos de post, tipos de gancho de la primera línea, uso de hilos, encuestas, imágenes o videos, "
        "a qué hora publican, cómo piden que las sigan, cómo responden comentarios, colaboraciones, etc. "
        "Quiero 5 estrategias concretas que pueda copiar Ezequiel Bagnato (trader, bots de trading, IA, cripto; publica noticias, "
        "preguntas, encuestas y enseñanzas en español rioplatense). Todo en español rioplatense, diciendo 'dinero' y nunca 'plata'. "
        "Nada que rompa las reglas de X (nada de seguir/dejar de seguir masivo, menciones automáticas o comprar seguidores). "
        "Respondé SOLO con JSON: {\"estrategias\": [{\"nombre\": \"en 3 a 6 palabras\", \"quien\": \"@cuenta que lo hace (1 o 2)\", "
        "\"ejemplo_url\": \"https://x.com/... de un post que lo muestra\", \"que_hacen\": \"1 o 2 oraciones\", "
        "\"como_aplicarlo\": \"1 o 2 oraciones concretas para Ezequiel\"}]}"
    )
    return preguntar(cfg, consigna, momento - timedelta(days=7), max_turnos=5)
