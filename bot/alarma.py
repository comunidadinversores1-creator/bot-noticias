"""Alarma de precio: si Bitcoin (o Ethereum) se mueve fuerte, sale un aviso SIN depender de la IA ni de que
algún medio lo publique. Los datos vienen de CoinGecko (gratis, sin clave). Todo se configura en
ajustes.yaml -> alarma_precios."""
from __future__ import annotations

import html

import requests

from .util import AGENTE, log

URL = "https://api.coingecko.com/api/v3/coins/markets"
NOMBRES = {"bitcoin": ("Bitcoin", "BTC", "bitcoin"), "ethereum": ("Ethereum", "ETH", "ethereum")}


def leer(ids: list[str]) -> dict[str, dict]:
    """{'bitcoin': {'precio': 64995.4, 'c24': -4.8, 'c7': -14.1, 'max24': ..., 'min24': ...}} o {} si falla."""
    try:
        r = requests.get(URL, params={"vs_currency": "usd", "ids": ",".join(ids), "price_change_percentage": "24h,7d"},
                         headers={"User-Agent": AGENTE, "Accept": "application/json"}, timeout=20)
        r.raise_for_status()
        datos = {}
        for m in r.json():
            datos[m["id"]] = {
                "precio": float(m["current_price"]), "c24": float(m.get("price_change_percentage_24h_in_currency") or m.get("price_change_percentage_24h") or 0),
                "c7": float(m.get("price_change_percentage_7d_in_currency") or 0),
                "max24": float(m.get("high_24h") or 0), "min24": float(m.get("low_24h") or 0),
            }
        return datos
    except Exception as error:                 # la alarma es un extra: nunca frena al resto
        log.warning("alarma de precios: no pude leer CoinGecko (%s)", error)
        return {}


def miles(x: float) -> str:
    return f"{x:,.0f}".replace(",", ".")


def pct(x: float) -> str:
    return f"{x:+.1f}%".replace(".", ",")


def nivel(precio: float, paso: float) -> float:
    """El escalón redondo por debajo (o por arriba) del precio: 64.995 con paso 1.000 -> 64.000."""
    return (precio // paso) * paso


def decidir(moneda: str, d: dict, mem: dict, cfg: dict) -> dict | None:
    """¿Hay que avisar? Sí si el movimiento de 24 h pasa el umbral y el precio cruzó un escalón redondo
    desde el último aviso de esa moneda. Devuelve {'sube': bool, 'nivel': float} o None."""
    umbral = float(cfg.get("umbral_24h", 4))
    paso = float((cfg.get("escalon") or {}).get(moneda, 1000 if moneda == "bitcoin" else 100))
    if abs(d["c24"]) < umbral:
        return None
    sube = d["c24"] > 0
    ultimo = mem.get(moneda) or {}
    escalon = nivel(d["precio"], paso) + (0 if sube else paso)       # cae: "perdió los 65.000"; sube: "superó los 64.000"
    if ultimo.get("nivel") == escalon and ultimo.get("sube") == sube:
        return None                                                  # ya avisamos ese escalón
    if ultimo and ultimo.get("sube") == sube:
        anterior = float(ultimo.get("nivel") or 0)
        if (not sube and escalon >= anterior) or (sube and escalon <= anterior):
            return None                                              # rebotó: no se avisa un escalón ya pasado
    return {"sube": sube, "nivel": escalon}


def textos(moneda: str, d: dict, dec: dict, enlace: str) -> tuple[str, str, str]:
    """(titular, Telegram en HTML, X)."""
    nombre, sigla, _ = NOMBRES[moneda]
    verbo = "SUPERÓ" if dec["sube"] else "PERDIÓ"
    titular = f"{nombre} {verbo.lower()} los USD {miles(dec['nivel'])}"
    emoji = "🚀" if dec["sube"] else "🚨"
    tg = (f"{emoji} <b>{html.escape(nombre.upper())} {verbo} LOS USD {miles(dec['nivel'])}</b>\n\n"
          f"Ahora está en <b>USD {miles(d['precio'])}</b>.\n\n"
          f"📊 En números:\n"
          f"1️⃣ 24 horas: {pct(d['c24'])}\n"
          f"2️⃣ 7 días: {pct(d['c7'])}\n"
          f"3️⃣ Rango del día: USD {miles(d['min24'])} a USD {miles(d['max24'])}\n\n"
          + ("🧠 Ojo con el apalancamiento: en caídas así las mechas barren a los que operan sin stop loss.\n\n" if not dec["sube"] else
             "🧠 Ojo con entrar tarde: después de subas fuertes suelen venir correcciones.\n\n")
          + "👇 ¿Qué hacés: comprás, vendés o esperás?\n\n"
          + (f'📈 <a href="{html.escape(enlace, quote=True)}">Operá {html.escape(nombre)} en Exness y te devuelvo el 20% de cashback en tus comisiones</a>\n\n' if enlace else "")
          + "<i>No es recomendación de inversión. Operar con apalancamiento implica riesgo de perder tu capital.</i>")
    x = (f"{emoji} {nombre} {verbo.lower()} los USD {miles(dec['nivel'])}.\n\n"
         f"ahora está en USD {miles(d['precio'])}.\n\n"
         f"📊 24 h: {pct(d['c24'])}\n📊 7 días: {pct(d['c7'])}\n\n"
         f"👇 ¿comprás, vendés o esperás?")
    return titular, tg, x


def _html(moneda: str, d: dict, dec: dict, alto: int) -> str:
    from pathlib import Path
    from . import robot
    from .carrusel_html import CSS_CARICATURA, _datos, _fuentes
    from .util import RAIZ
    nombre, sigla, logo = NOMBRES[moneda]
    try:
        img = _datos((RAIZ / "assets" / "marcas" / f"{logo}.png").read_bytes(), "image/png")
    except OSError:
        img = ""
    color = "#16a34a" if dec["sube"] else "#ef4444"
    verbo = "superó" if dec["sube"] else "perdió"
    pad = "250px 50px 280px" if alto > 1500 else "60px 50px"
    caja = lambda v, t: f'<div class="d"><b>{v}</b><span>{t}</span></div>'
    return f"""<!doctype html><html><head><meta charset=utf-8><style>{_fuentes()}{CSS_CARICATURA}
    section{{display:block;width:1080px;height:{alto}px}}
    .c{{width:1080px;height:{alto}px;padding:{pad};justify-content:space-evenly}} .c h2{{font-size:{116 if alto > 1500 else 100}px}} .c .r{{color:{color}}}
    .fila{{display:flex;align-items:center;gap:30px}} .lg{{width:230px;height:230px;border-radius:50%;background:#fff;border:7px solid #111;display:flex;align-items:center;justify-content:center;box-shadow:8px 8px 0 #111}} .lg img{{max-width:160px}}
    .dib svg{{height:300px;width:auto}}
    .datos{{display:grid;grid-template-columns:1fr 1fr;gap:20px;width:960px}}
    .d{{background:#fff;border:6px solid #111;border-radius:26px;box-shadow:7px 7px 0 #111;padding:20px;text-align:center}}
    .d b{{display:block;font-family:Luck;font-size:72px;color:{color};line-height:1}} .d span{{font-family:Archivo;font-size:30px}}
    .chip{{background:{color} !important;font-size:34px !important}}
    </style></head><body><section><div class="c">
    <div style="display:flex;flex-direction:column;align-items:center"><div class="chip">{"Se dispara" if dec["sube"] else "Última hora"}</div>
    <h2>{html.escape(nombre)} {verbo} los <span class="r">{miles(dec['nivel'] / 1000) + " mil" if dec['nivel'] >= 10000 else miles(dec['nivel'])}</span></h2></div>
    <div class="fila">{f'<div class="lg"><img src="{img}"></div>' if img else ""}<div class="dib">{robot.svg("festejo" if dec["sube"] else "alerta", 420, "")}</div></div>
    <div class="datos">{caja("USD " + miles(d["precio"]), "precio ahora")}{caja(pct(d["c24"]), "en 24 horas")}{caja(pct(d["c7"]), "en 7 días")}{caja(miles(d["min24"]), "mínimo del día")}</div>
    </div></section></body></html>"""


def imagenes(moneda: str, d: dict, dec: dict) -> tuple[bytes | None, bytes | None]:
    """(post 1080x1350, historia 1080x1920). Si falla el navegador, (None, None): el aviso sale solo con texto."""
    try:
        from .historias import sacar_foto
        return sacar_foto(_html(moneda, d, dec, 1350), 1080, 1350), sacar_foto(_html(moneda, d, dec, 1920), 1080, 1920)
    except Exception as error:
        log.warning("alarma de precios: no pude armar la imagen (%s)", error)
        return None, None
