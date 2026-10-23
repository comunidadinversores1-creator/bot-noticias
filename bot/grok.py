"""Grok (xAI): la única IA que lee X en tiempo real. Se usa para dos cosas:
1) saber de qué se está hablando HOY en X, para que las preguntas salgan sobre temas calientes;
2) encontrar posts virales del nicho, para que Eze les responda a mano (responder automático
   a otras personas está prohibido por las reglas de X).

Si no está la llave XAI_API_KEY, el bot sigue como siempre (sin estas dos cosas)."""
from __future__ import annotations

import json
import os
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
        "Nada de fútbol, farándula ni política partidaria (salvo que mueva los mercados). "
        "Respondé SOLO con JSON: {\"temas\": [{\"tema\": \"en 5 a 10 palabras\", \"contexto\": \"qué pasó, en 1 o 2 oraciones, solo hechos\", "
        "\"hashtag\": \"el hashtag que más se usa para ese tema, sin #, o vacío\"}]}"
    )
    datos = preguntar(cfg, consigna, momento - timedelta(days=1))
    temas = []
    for t in datos.get("temas") or []:
        if isinstance(t, dict) and str(t.get("tema") or "").strip():
            temas.append({k: str(t.get(k) or "").strip()[:300] for k in ("tema", "contexto", "hashtag")})
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
