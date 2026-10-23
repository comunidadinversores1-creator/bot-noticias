"""Calendario económico: la agenda del día y el aviso antes de cada dato importante
(inflación, empleo, tasas...), con la advertencia de que puede haber más volatilidad.

Los datos salen del calendario público de Forex Factory (un archivo con los eventos de la semana).
Si no responde, el bot sigue con lo demás y no publica nada del calendario."""
from __future__ import annotations

import html
import re
from datetime import datetime, timedelta

import requests

from .util import log, normalizar, zona

URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"

PAISES = {
    "USD": ("🇺🇸", "EE.UU.", "el dólar, el oro, Wall Street y las criptomonedas"),
    "EUR": ("🇪🇺", "la zona euro", "el euro y pares como EUR/USD y EUR/AUD"),
    "AUD": ("🇦🇺", "Australia", "el dólar australiano y pares como EUR/AUD y AUD/CAD"),
    "CAD": ("🇨🇦", "Canadá", "el dólar canadiense y pares como AUD/CAD"),
    "CNY": ("🇨🇳", "China", "las materias primas, el dólar australiano y las bolsas"),
    "GBP": ("🇬🇧", "Reino Unido", "la libra y pares como GBP/USD"),
    "JPY": ("🇯🇵", "Japón", "el yen y pares como USD/JPY"),
    "CHF": ("🇨🇭", "Suiza", "el franco suizo"),
    "NZD": ("🇳🇿", "Nueva Zelanda", "el dólar neozelandés"),
}

# De lo más específico a lo más general (el primero que coincide gana).
NOMBRES = [
    ("core pce", "inflación PCE núcleo"),
    ("pce price", "inflación PCE"),
    ("core cpi", "inflación núcleo (IPC)"),
    ("cpi", "inflación (IPC)"),
    ("core ppi", "precios mayoristas núcleo (IPP)"),
    ("ppi", "precios mayoristas (IPP)"),
    ("non-farm employment change", "empleo no agrícola (NFP)"),
    ("unemployment claims", "pedidos de subsidio por desempleo"),
    ("unemployment rate", "tasa de desempleo"),
    ("employment change", "cambio en el empleo"),
    ("jolts", "vacantes laborales (JOLTS)"),
    ("federal funds rate", "decisión de tasas de la Fed"),
    ("fomc meeting minutes", "minutas de la Fed (FOMC)"),
    ("fomc", "comunicado de la Fed (FOMC)"),
    ("fed chair", "discurso del presidente de la Fed"),
    ("main refinancing rate", "decisión de tasas del BCE"),
    ("ecb president", "discurso de la presidenta del BCE"),
    ("cash rate", "decisión de tasas del banco central de Australia"),
    ("overnight rate", "decisión de tasas del banco central de Canadá"),
    ("official bank rate", "decisión de tasas del Banco de Inglaterra"),
    ("boj policy rate", "decisión de tasas del Banco de Japón"),
    ("monetary policy", "decisión de política monetaria"),
    ("advance gdp", "PBI (primer dato)"),
    ("gdp", "PBI"),
    ("core retail sales", "ventas minoristas núcleo"),
    ("retail sales", "ventas minoristas"),
    ("ism manufacturing", "PMI manufacturero ISM"),
    ("ism services", "PMI de servicios ISM"),
    ("manufacturing pmi", "PMI manufacturero"),
    ("services pmi", "PMI de servicios"),
    ("consumer confidence", "confianza del consumidor"),
    ("consumer sentiment", "confianza del consumidor"),
    ("trade balance", "balanza comercial"),
    ("industrial production", "producción industrial"),
]


def nombre_en_espanol(titulo: str) -> str:
    bajo = titulo.lower()
    for clave, nombre in NOMBRES:
        if clave in bajo:
            return nombre
    return titulo


def leer(cfg: dict, momento: datetime) -> list[dict]:
    """Los eventos de la semana que importan (por impacto y país), en hora de Argentina."""
    cal = cfg.get("calendario") or {}
    impactos = {str(i).lower() for i in (cal.get("impacto") or ["High"])}
    paises = {str(p).upper() for p in (cal.get("paises") or PAISES.keys())}
    try:
        r = requests.get(str(cal.get("url") or URL), timeout=25, headers={"User-Agent": "Mozilla/5.0 (bot-noticias)"})
        crudos = r.json() if r.status_code == 200 else []
    except (requests.RequestException, ValueError) as error:
        log.warning("calendario económico: no respondió (%s)", type(error).__name__)
        return []
    if not isinstance(crudos, list):
        return []
    eventos = []
    for e in crudos:
        if not isinstance(e, dict):
            continue
        titulo, pais = str(e.get("title") or "").strip(), str(e.get("country") or "").upper()
        if not titulo or pais not in paises or str(e.get("impact") or "").lower() not in impactos:
            continue
        if "holiday" in titulo.lower():
            continue
        try:
            fecha = datetime.fromisoformat(str(e.get("date"))).astimezone(zona(cfg))
        except (TypeError, ValueError):
            continue
        eventos.append({
            "titulo": titulo, "pais": pais, "fecha": fecha, "nombre": nombre_en_espanol(titulo),
            "prevision": str(e.get("forecast") or "").strip(), "anterior": str(e.get("previous") or "").strip(),
            "clave": f"{pais}|{titulo}|{fecha.isoformat()}",
        })
    eventos.sort(key=lambda e: e["fecha"])
    return eventos


def de_hoy(eventos: list[dict], cfg: dict, momento: datetime) -> list[dict]:
    hoy = momento.astimezone(zona(cfg)).date()
    return [e for e in eventos if e["fecha"].date() == hoy]


def proximos(eventos: list[dict], momento: datetime, desde_min: int = 10, hasta_min: int = 80) -> list[dict]:
    """Los que salen dentro de un rato (para el aviso previo). Si hay varios a la misma hora, van juntos."""
    salida = []
    for e in eventos:
        minutos = (e["fecha"] - momento).total_seconds() / 60
        if desde_min <= minutos <= hasta_min:
            salida.append(e)
    if not salida:
        return []
    primera = salida[0]["fecha"]
    return [e for e in salida if e["fecha"] == primera]


def _cifras(e: dict) -> str:
    partes = []
    if e["prevision"]:
        partes.append(f"Previsión: {e['prevision']}")
    if e["anterior"]:
        partes.append(f"Anterior: {e['anterior']}")
    return " · ".join(partes)


def _activos(eventos: list[dict]) -> str:
    vistos = []
    for e in eventos:
        texto = PAISES.get(e["pais"], ("", "", "los mercados"))[2]
        if texto not in vistos:
            vistos.append(texto)
    return "; ".join(vistos[:2])


AVISO = "Cuando salen estos datos puede haber movimientos bruscos y spreads más amplios en {activos}. Si operás, cuidá tu gestión de riesgo."


def agenda(eventos: list[dict], titulo: str = "Agenda económica de hoy") -> tuple[str, str]:
    """(Telegram con formato, texto plano para X)."""
    e_ = lambda s: html.escape(s, quote=False)
    tg = [f"📅 <b>{e_(titulo)}</b> (hora Argentina)", ""]
    x = [f"📅 {titulo} (hora Argentina)", ""]
    for e in eventos[:8]:
        bandera, pais, _ = PAISES.get(e["pais"], ("🌍", e["pais"], ""))
        linea = f"🕘 {e['fecha']:%H:%M} · {bandera} {pais}: {e['nombre']}"
        tg.append(e_(linea))
        x.append(linea)
        if _cifras(e):
            tg.append(f"   <i>{e_(_cifras(e))}</i>")
    aviso = "⚠️ " + AVISO.format(activos=_activos(eventos))
    tg += ["", e_(aviso)]
    x += ["", aviso]
    return "\n".join(tg), "\n".join(x)


def aviso_previo(eventos: list[dict], momento: datetime) -> tuple[str, str]:
    e_ = lambda s: html.escape(s, quote=False)
    primero = eventos[0]
    minutos = max(1, round((primero["fecha"] - momento).total_seconds() / 60 / 5) * 5)
    cuando = "En 1 hora" if 55 <= minutos <= 65 else (f"En {minutos} minutos" if minutos < 60 else f"En {minutos // 60} h {minutos % 60} min")
    bandera, pais, _ = PAISES.get(primero["pais"], ("🌍", primero["pais"], ""))
    nombres = " y ".join(dict.fromkeys(e["nombre"] for e in eventos))
    cabeza = f"⏰ {cuando}: {nombres} de {pais} {bandera}"
    tg = [f"<b>{e_(cabeza)}</b>", "", e_(f"🕘 Sale a las {primero['fecha']:%H:%M} (hora Argentina)")]
    x = [cabeza, "", f"🕘 Sale a las {primero['fecha']:%H:%M} (hora Argentina)"]
    for e in eventos[:3]:
        if _cifras(e):
            linea = f"📊 {e['nombre']}: {_cifras(e)}" if len(eventos) > 1 else f"📊 {_cifras(e)}"
            tg.append(e_(linea))
            x.append(linea)
    explicacion = (f"Si el dato sale muy distinto a la previsión, suele mover fuerte a {_activos(eventos)}.")
    aviso = "⚠️ Puede haber más volatilidad y spreads más amplios en ese momento. Si operás, cuidá tu gestión de riesgo."
    tg += ["", e_(explicacion), "", e_(aviso)]
    x += ["", explicacion, "", aviso]
    return "\n".join(tg), "\n".join(x)


def para_hashtags(eventos: list[dict]) -> dict:
    """Un 'post' mínimo para elegir los hashtags (#Inflación, #Fed...)."""
    return {"titular": " ".join(normalizar(e["nombre"]) + " " + e["titulo"] for e in eventos), "resumen": "", "categoria": ""}


def limpiar_x(texto: str) -> str:
    """EE.UU. y los números con punto no se vuelven links; por las dudas se cortan los 'palabra.palabra'."""
    return re.sub(r"(?<=[a-záéíóú])\.(?=[a-záéíóú]{2,})", ".​", texto)
