"""Búsqueda gratis para cuando Grok no está (sin saldo o apagado): Gemini con la búsqueda de Google.

No lee X. Lo que sí hace, sin costo: encuentra de qué se habla HOY en la web (noticias y tendencias) y arma
links de búsqueda en X para que Eze vea los posts que más se mueven y responda a mano."""
from __future__ import annotations

import os
import time
from urllib.parse import quote_plus

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


def buscar(cfg: dict, consigna: str, con_busqueda: bool = True) -> dict:
    clave = (os.environ.get("GEMINI_API_KEY") or "").strip()
    if not clave:
        raise SinBusqueda("falta GEMINI_API_KEY")
    modelos = list(((cfg.get("ia") or {}).get("modelos") or {}).get("gemini") or []) + ["gemini-2.5-flash"]
    cuerpo = {"contents": [{"role": "user", "parts": [{"text": consigna}]}], "generationConfig": {"temperature": 0.4}}
    if con_busqueda:
        cuerpo["tools"] = [{"google_search": {}}]
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
            log.warning("Búsqueda gratis, %s respondió %s: %s", modelo, r.status_code, r.text[:300].replace("\n", " "))
            if r.status_code in (500, 502, 503, 504):
                time.sleep(3 if intento == 0 else 6)
                continue
            break                                  # 400/403/404/429: se prueba con el modelo que sigue
    log.warning("Búsqueda gratis: %s", ultimo)
    raise SinBusqueda(ultimo)


def temas_del_dia(cfg: dict) -> tuple[list[dict], list[str]]:
    areas = str((cfg.get("grok") or {}).get("areas") or AREAS)
    datos = buscar(cfg, (
        f"Usá la búsqueda de Google para ver de qué se está hablando HOY (últimas 24 horas), en Argentina y en el mundo, sobre: {areas}. "
        "Quiero los 6 temas con más conversación y que muevan o puedan mover los mercados o el dinero de la gente. "
        "Nada de fútbol ni farándula. En el contexto nombrá a las personas, empresas y causas involucradas, con hechos verificables "
        "y en potencial si hay acusaciones. No inventes nada que no encuentres. "
        "Respondé SOLO con JSON: {\"temas\": [{\"tema\": \"en 5 a 10 palabras\", \"contexto\": \"qué pasó, en 1 o 2 oraciones, solo hechos\", "
        "\"hashtag\": \"el hashtag más usado para ese tema, sin #, o vacío\", "
        "\"cuentas\": [\"usuarios oficiales de X de las personas o empresas involucradas, sin @, solo si estás seguro\"]}]}"
    ))
    return datos.get("temas") or [], []


def virales(cfg: dict, cantidad: int) -> list[dict]:
    """Sin leer X: los temas con más conversación de hoy + un link que abre esa búsqueda en X, ordenada por lo más visto."""
    areas = str((cfg.get("grok") or {}).get("areas") or AREAS)
    datos = buscar(cfg, (
        f"Usá la búsqueda de Google para encontrar los {cantidad} temas con más conversación HOY (últimas 24 horas) sobre: {areas}, "
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


def inspiracion(cfg: dict, cantidad: int) -> list[dict]:
    """Borradores originales con formatos que se sabe que funcionan en X, sobre los temas de hoy."""
    areas = str((cfg.get("grok") or {}).get("areas") or AREAS)
    datos = buscar(cfg, (
        f"Usá la búsqueda de Google para ver los temas de HOY sobre {areas}. Después escribí {cantidad} borradores ORIGINALES para la cuenta de X "
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
    ), con_busqueda=False)
    cita = str(datos.get("cita") or "").strip()[:300]
    if not cita:
        return None
    return {"url": link_x(" ".join(str(titular).split()[:7])), "cuenta": "los posts más vistos", "cita": cita}


def temas_del_dia_limpio(cfg: dict) -> tuple[list[dict], list[str]]:
    """Igual que el de Grok: temas ya limpios (hasta 8) y los ganchos (acá no hay: no se lee X)."""
    import re
    salida = []
    for t in temas_del_dia(cfg)[0]:
        if isinstance(t, dict) and str(t.get("tema") or "").strip():
            item = {k: str(t.get(k) or "").strip()[:300] for k in ("tema", "contexto", "hashtag")}
            item["cuentas"] = [re.sub(r"[^\w]", "", str(c)) for c in (t.get("cuentas") or []) if re.sub(r"[^\w]", "", str(c))][:3]
            salida.append(item)
    return salida[:8], []
