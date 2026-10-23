"""Lee las fuentes (RSS) y devuelve las noticias en un formato único."""
from __future__ import annotations

import hashlib
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone

import feedparser
import requests

from .util import AGENTE, ahora_utc, limpiar_html, log, normalizar, socios_activos

MAX_POR_FUENTE = 40


@dataclass
class Noticia:
    id: str
    titulo: str
    resumen: str
    enlace: str
    fuente: str
    fecha: datetime          # siempre en UTC
    idioma: str = "es"
    peso: float = 1.0
    tema: str = ""
    cobertura: int = 1       # cuántos medios la levantaron (dato de Google Noticias)
    horas: float = 0         # hasta cuántas horas vale (0 = la regla general)

    def a_dict(self) -> dict:
        d = asdict(self)
        d["fecha"] = self.fecha.isoformat()
        return d

    @classmethod
    def de_dict(cls, d: dict) -> "Noticia":
        d = dict(d)
        d["fecha"] = datetime.fromisoformat(d["fecha"])
        conocidos = {c for c in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in conocidos})


def _id(titulo: str, fuente: str) -> str:
    base = normalizar(titulo) + "|" + normalizar(fuente)
    return hashlib.sha1(base.encode("utf-8")).hexdigest()[:16]


def _fecha(entrada) -> datetime | None:
    for campo in ("published_parsed", "updated_parsed", "created_parsed"):
        t = entrada.get(campo)
        if t:
            try:
                return datetime(*t[:6], tzinfo=timezone.utc)
            except (ValueError, TypeError):
                continue
    return None


def _nombre_limpio(nombre: str) -> str:
    """'Infobae.com' -> 'Infobae' (así X no lo toma como un link)."""
    nombre = re.sub(r"\s+", " ", nombre or "").strip()
    nombre = re.sub(r"(?i)^www\.", "", nombre)
    nombre = re.sub(r"(?i)\.(com|net|org|info|io|co|tv)(\.[a-z]{2})?$", "", nombre)
    nombre = re.sub(r"(?i)\.(ar|es|mx|cl|uy|us|uk)$", "", nombre)
    return nombre.strip() or "Agencias"


def interpretar(contenido: bytes, fuente: dict, ahora: datetime | None = None) -> list[Noticia]:
    """Convierte el contenido de un feed en noticias."""
    ahora = ahora or ahora_utc()
    es_google = "news.google." in fuente.get("url", "")
    datos = feedparser.parse(contenido)
    salida: list[Noticia] = []
    for e in datos.entries[:MAX_POR_FUENTE]:
        titulo = limpiar_html(e.get("title"), 300)
        fecha = _fecha(e)
        if len(titulo) < 15 or fecha is None:
            continue
        if fecha > ahora + timedelta(hours=1):      # feeds con la zona horaria mal puesta
            fecha = ahora
        nombre = fuente.get("nombre", "Agencias")
        bruto = e.get("summary") or e.get("description") or ""
        cobertura = 1
        if es_google:
            origen = (e.get("source") or {}).get("title") or ""
            if origen:
                nombre = _nombre_limpio(origen)
                sufijo = " - " + origen
                if titulo.endswith(sufijo):
                    titulo = titulo[: -len(sufijo)].strip()
            cobertura = max(1, bruto.count("<li"))
            resumen = ""                              # Google solo trae titulares
        else:
            resumen = limpiar_html(bruto, 700)
            if normalizar(resumen) == normalizar(titulo) or len(resumen) < 25:
                resumen = ""
        salida.append(
            Noticia(
                id=_id(titulo, nombre),
                titulo=titulo,
                resumen=resumen,
                enlace=e.get("link") or "",
                fuente=nombre,
                fecha=fecha,
                idioma=fuente.get("idioma", "es"),
                peso=float(fuente.get("peso", 1.0)),
                tema=fuente.get("tema", "") or "",
                cobertura=cobertura,
                horas=float(fuente.get("horas", 0) or 0),
            )
        )
    return salida


def _bajar(fuente: dict, ahora: datetime | None = None) -> tuple[dict, list[Noticia], str]:
    try:
        r = requests.get(
            fuente["url"],
            headers={
                "User-Agent": AGENTE,
                "Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, */*;q=0.8",
                "Accept-Language": "es-AR,es;q=0.9,en;q=0.8",
            },
            timeout=(6, 14),
        )
        if r.status_code != 200:
            return fuente, [], f"respondió {r.status_code}"
        noticias = interpretar(r.content, fuente, ahora)
        return fuente, noticias, "" if noticias else "sin noticias con fecha"
    except requests.RequestException as error:
        return fuente, [], type(error).__name__
    except Exception as error:  # un feed roto nunca debe frenar al bot
        return fuente, [], f"error al leer: {type(error).__name__}"


def leer_fuentes(cfg: dict, ahora: datetime | None = None) -> tuple[list[Noticia], list[dict]]:
    """Baja todas las fuentes en paralelo. Devuelve (noticias, informe de salud)."""
    # Una fuente marcada con 'socio: x' es una búsqueda dedicada a esa marca: si la marca está apagada, no se lee.
    activos = socios_activos(cfg)
    fuentes = [f for f in cfg["fuentes"] if f.get("url") and (not f.get("socio") or str(f["socio"]) in activos)]
    noticias: list[Noticia] = []
    salud: list[dict] = []
    with ThreadPoolExecutor(max_workers=12) as grupo:
        for fuente, items, error in grupo.map(lambda f: _bajar(f, ahora), fuentes):
            salud.append({"nombre": fuente.get("nombre"), "url": fuente["url"], "noticias": len(items), "error": error})
            noticias.extend(items)
    bien = sum(1 for s in salud if s["noticias"])
    log.info("Fuentes: %d de %d respondieron, %d noticias en total", bien, len(salud), len(noticias))
    for s in salud:
        if s["error"]:
            log.warning("  fuente con problema: %s (%s) -> %s", s["nombre"], s["url"][:70], s["error"])
    return noticias, salud
