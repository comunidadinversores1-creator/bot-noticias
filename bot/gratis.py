"""Búsqueda gratis para cuando Grok no está (sin saldo o apagado).

No lee X (solo Grok y la API de X lo hacen). Lo que sí hace, sin costo: junta los titulares de hoy de las fuentes
del bot y las tendencias de búsqueda de Google Argentina, le pide a Gemini (sin herramientas, así no hay cuota de
búsqueda que se agote) que elija los temas con más conversación, y arma links de búsqueda en X para que Eze vea
los posts que más se mueven y responda a mano."""
from __future__ import annotations

import os
import re
import time
from datetime import datetime
from urllib.parse import quote_plus

import feedparser
import requests

from .ia import extraer_json
from .util import log

BASE = "https://generativelanguage.googleapis.com/v1beta/models"
AREAS = ("trading, criptomonedas, exchanges, inteligencia artificial, empresas tecnológicas, bolsa, "
         "economía, guerras y conflictos que mueven los mercados, dólar, dinero y ahorro")


class SinBusqueda(Exception):
    pass


def disponible() -> bool:
    return bool((os.environ.get("GEMINI_API_KEY") or "").strip())


_CACHE: dict = {}


def tendencias_google() -> list[str]:
    """Lo más buscado hoy en Google Argentina (gratis, sin llave)."""
    try:
        r = requests.get("https://trends.google.com/trending/rss?geo=AR", timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200:
            return []
        return [re.sub(r"\s+", " ", str(e.get("title") or "")).strip() for e in feedparser.parse(r.content).entries[:25] if e.get("title")]
    except Exception as error:                      # es un extra: si no responde, se sigue sin esto
        log.warning("Tendencias de Google: %s", error)
        return []


def titulares_de_hoy(cfg: dict, ahora: datetime | None = None) -> str:
    """Los titulares de las últimas horas de las fuentes del bot + lo más buscado en Google, en texto para la IA."""
    if "texto" in _CACHE and _CACHE.get("cuando") == (ahora or datetime.now()).strftime("%Y-%m-%d %H"):
        return _CACHE["texto"]
    from datetime import timedelta, timezone
    from .fuentes import leer_fuentes
    ahora = ahora or datetime.now(timezone.utc)
    try:
        noticias, _ = leer_fuentes(cfg, ahora)
    except Exception as error:
        log.warning("Titulares para la búsqueda gratis: %s", error)
        noticias = []
    recientes = [n for n in noticias if ahora - n.fecha <= timedelta(hours=30)]
    recientes.sort(key=lambda n: (-int(getattr(n, "cobertura", 1) or 1), -n.fecha.timestamp()))
    lineas, vistos = [], set()
    for n in recientes:
        clave = re.sub(r"\W+", " ", n.titulo.lower())[:60]
        if clave in vistos:
            continue
        vistos.add(clave)
        cob = f" ({n.cobertura} medios)" if int(getattr(n, "cobertura", 1) or 1) > 1 else ""
        lineas.append(f"- {n.titulo.strip()[:160]} [{n.fuente}]{cob}")
        if len(lineas) >= 45:
            break
    tendencias = tendencias_google()
    texto = ("TITULARES DE HOY (de medios):\n" + "\n".join(lineas) if lineas else "TITULARES DE HOY: no hay.") + \
            ("\n\nLO MÁS BUSCADO HOY EN GOOGLE ARGENTINA:\n" + "\n".join(f"- {t}" for t in tendencias) if tendencias else "")
    _CACHE.update({"texto": texto, "cuando": ahora.strftime("%Y-%m-%d %H")})
    return texto


def link_x(busqueda: str) -> str:
    """Búsqueda en X ordenada por lo más visto: https://x.com/search?q=...&f=top"""
    return "https://x.com/search?q=" + quote_plus(str(busqueda).strip()) + "&f=top"


def _texto(datos: dict) -> str:
    partes = []
    for cand in datos.get("candidates") or []:
        for p in (cand.get("content") or {}).get("parts") or []:
            if isinstance(p, dict) and isinstance(p.get("text"), str) and not p.get("thought"):
                partes.append(p["text"])
        if partes:
            break
    return "\n".join(partes)


def buscar(cfg: dict, consigna: str) -> dict:
    clave = (os.environ.get("GEMINI_API_KEY") or "").strip()
    if not clave:
        raise SinBusqueda("falta GEMINI_API_KEY")
    modelos = list(((cfg.get("ia") or {}).get("modelos") or {}).get("gemini") or [])
    cuerpo = {"contents": [{"role": "user", "parts": [{"text": consigna}]}], "generationConfig": {"temperature": 0.4}}
    ultimo = "sin respuesta"
    for modelo in dict.fromkeys(modelos):
        for intento in range(2):
            try:
                r = requests.post(f"{BASE}/{modelo}:generateContent", json=cuerpo, timeout=(10, 90),
                                  headers={"x-goog-api-key": clave, "Content-Type": "application/json"})
            except requests.RequestException as error:
                ultimo = f"{modelo}: {type(error).__name__}"
                time.sleep(2)
                continue
            if r.status_code == 200:
                try:
                    datos = extraer_json(_texto(r.json()))
                except ValueError:
                    datos = None
                if isinstance(datos, dict):
                    return datos
                ultimo = f"{modelo}: no devolvió el formato pedido"
                break
            ultimo = f"{modelo}: error {r.status_code}"
            log.warning("Búsqueda gratis, %s respondió %s: %s", modelo, r.status_code, r.text[:1500].replace("\n", " "))
            if r.status_code in (500, 502, 503, 504):
                time.sleep(3 if intento == 0 else 6)
                continue
            break                                  # 400/403/404/429: se prueba con el modelo que sigue
    log.warning("Búsqueda gratis: %s", ultimo)
    raise SinBusqueda(ultimo)


def temas_del_dia(cfg: dict, ahora: datetime | None = None) -> tuple[list[dict], list[str]]:
    areas = str((cfg.get("grok") or {}).get("areas") or AREAS)
    datos = buscar(cfg, (
        f"{titulares_de_hoy(cfg, ahora)}\n\n"
        f"Con SOLO esa información, elegí de qué se está hablando HOY sobre: {areas}. "
        "Quiero los 6 temas con más conversación (los que aparecen en varios medios o en lo más buscado) y que muevan o puedan mover los mercados o el dinero de la gente. "
        "Nada de fútbol ni farándula. En el contexto nombrá a las personas, empresas y causas involucradas, solo con hechos que figuren en los titulares "
        "y en potencial si hay acusaciones. No inventes nada que no esté ahí. "
        "Respondé SOLO con JSON: {\"temas\": [{\"tema\": \"en 5 a 10 palabras\", \"contexto\": \"qué pasó, en 1 o 2 oraciones, solo hechos\", "
        "\"hashtag\": \"el hashtag más usado para ese tema, sin #, o vacío\", "
        "\"cuentas\": [\"usuarios oficiales de X de las personas o empresas involucradas, sin @, solo si estás seguro\"]}]}"
    ))
    return datos.get("temas") or [], []


def virales(cfg: dict, cantidad: int, ahora: datetime | None = None) -> list[dict]:
    """Sin leer X: los temas con más conversación de hoy + un link que abre esa búsqueda en X, ordenada por lo más visto."""
    areas = str((cfg.get("grok") or {}).get("areas") or AREAS)
    datos = buscar(cfg, (
        f"{titulares_de_hoy(cfg, ahora)}\n\n"
        f"Con SOLO esa información, elegí los {cantidad} temas con más conversación HOY sobre: {areas}, "
        "donde una buena respuesta de un trader pueda ganar visibilidad en X. Nada de estafas, sorteos o spam. "
        "Para cada uno: 'busqueda' = 2 a 4 palabras clave para buscarlo en X (sin hashtags ni comillas), 'de_que_habla' = qué pasó, 1 oración con hechos, "
        "'idea' = una respuesta corta que aporte (un dato, una experiencia o una pregunta), en español rioplatense, sin promocionar nada y "
        "diciendo 'dinero', nunca 'plata'. "
        "Respondé SOLO con JSON: {\"posts\": [{\"busqueda\": \"...\", \"de_que_habla\": \"...\", \"idea\": \"...\"}]}"
    ))
    salida = []
    for p in datos.get("posts") or []:
        if isinstance(p, dict) and str(p.get("busqueda") or "").strip():
            b = str(p["busqueda"]).strip()[:80]
            salida.append({"url": link_x(b), "cuenta": f"Ver en X: {b}", "de_que_habla": str(p.get("de_que_habla") or "").strip()[:400],
                           "por_que": "Tema caliente de hoy: abrí el link, mirá los posts que más se mueven y respondé a uno.",
                           "idea": str(p.get("idea") or "").strip()[:400]})
    return salida[:cantidad]


def inspiracion(cfg: dict, cantidad: int, ahora: datetime | None = None) -> list[dict]:
    """Borradores originales con formatos que se sabe que funcionan en X, sobre los temas de hoy."""
    areas = str((cfg.get("grok") or {}).get("areas") or AREAS)
    datos = buscar(cfg, (
        f"{titulares_de_hoy(cfg, ahora)}\n\n"
        f"Mirá los temas de HOY sobre {areas}. Después escribí {cantidad} borradores ORIGINALES para la cuenta de X "
        "de Ezequiel Bagnato (trader, bots de trading, IA, cripto), cada uno con un formato distinto de los que se sabe que funcionan en X: "
        "una pregunta que cualquiera pueda contestar, un dilema con montos, una lista guardable, una cuenta con números o una opinión que divida. "
        "Reglas: español rioplatense, en minúscula, frases cortas con renglón en blanco entre ideas, sin emojis ni hashtags ni links, "
        "decir 'dinero' y nunca 'plata', no prometer ganancias, no contar casos inventados como reales (nada de 'conocí a un pibe'), "
        "no recomendar comprar ni vender un activo concreto, y no inventar cifras de los temas. "
        "Respondé SOLO con JSON: {\"posts\": [{\"formato\": \"pregunta, dilema, lista, cuentas u opinión\", \"borrador\": \"el post\"}]}"
    ))
    salida = []
    for p in datos.get("posts") or []:
        if isinstance(p, dict) and str(p.get("borrador") or "").strip():
            salida.append({"url": "", "cuenta": "", "por_que_funciono": f"formato probado: {str(p.get('formato') or '').strip()[:60]}",
                           "borrador": str(p["borrador"]).strip()[:1200]})
    return salida[:cantidad]


def para_citar(cfg: dict, titular: str, resumen: str) -> dict | None:
    datos = buscar(cfg, (
        f"Esta noticia acaba de salir: \"{titular}\". {resumen}\n"
        "Escribí una cita corta (máximo 200 caracteres) con la mirada de un trader: qué significa para el mercado o qué conviene mirar, "
        "sin predecir precios ni recomendar comprar o vender, en español rioplatense y diciendo 'dinero', nunca 'plata'. "
        "Respondé SOLO con JSON: {\"cita\": \"...\"}"
    ))
    cita = str(datos.get("cita") or "").strip()[:300]
    if not cita:
        return None
    return {"url": link_x(" ".join(str(titular).split()[:7])), "cuenta": "los posts más vistos", "cita": cita}


def temas_del_dia_limpio(cfg: dict, ahora: datetime | None = None) -> tuple[list[dict], list[str]]:
    """Igual que el de Grok: temas ya limpios (hasta 8) y los ganchos (acá no hay: no se lee X)."""
    salida = []
    for t in temas_del_dia(cfg, ahora)[0]:
        if isinstance(t, dict) and str(t.get("tema") or "").strip():
            item = {k: str(t.get(k) or "").strip()[:300] for k in ("tema", "contexto", "hashtag")}
            item["cuentas"] = [re.sub(r"[^\w]", "", str(c)) for c in (t.get("cuentas") or []) if re.sub(r"[^\w]", "", str(c))][:3]
            salida.append(item)
    return salida[:8], []
