"""Utilidades compartidas: registro, ajustes, textos y fechas."""
from __future__ import annotations

import html
import logging
import os
import re
import unicodedata
from datetime import datetime, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

RAIZ = Path(__file__).resolve().parent.parent
log = logging.getLogger("bot")

# El bot siempre se identifica como bot y da un contacto público (es lo que piden los medios y Wikimedia).
AGENTE = "Mozilla/5.0 (compatible; NoticiasCI-Bot/1.0; +https://x.com/C_INVERSORES)"
AGENTE_WIKI = "NoticiasCI-Bot/1.0 (https://x.com/C_INVERSORES) python-requests"

MESES = ["ENE", "FEB", "MAR", "ABR", "MAY", "JUN", "JUL", "AGO", "SEP", "OCT", "NOV", "DIC"]
ESPACIO_DURO = chr(0xA0)          # espacio que no se corta
ESPACIO_INVISIBLE = chr(0x200B)   # espacio de ancho cero


def configurar_log() -> None:
    logging.basicConfig(
        level=os.environ.get("NIVEL_LOG", "INFO"),
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )
    # requests/urllib3 escriben la URL completa (con el token de Telegram) en modo DEBUG.
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    if os.environ.get("GITHUB_ACTIONS") == "true":
        logging.getLogger().addHandler(_AvisoGitHub())


class _AvisoGitHub(logging.Handler):
    """En GitHub, los avisos y errores también quedan como cartelitos en el resumen de la corrida
    (se ven sin abrir el registro completo)."""

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)

    def emit(self, record: logging.LogRecord) -> None:
        tipo = "error" if record.levelno >= logging.ERROR else "warning"
        texto = self.format(record).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        print(f"::{tipo}::{texto[:900]}", flush=True)


def verdadero(valor) -> bool:
    """Para los interruptores de ajustes.yaml. Solo vale un sí bien claro: true, "true", "si"...
    Un "false" entre comillas (que para el programa sería un texto, o sea 'encendido') cuenta como no."""
    return valor is True or (isinstance(valor, str) and valor.strip().lower() in ("true", "si", "sí", "yes", "on"))


def socios_activos(cfg: dict) -> dict:
    """Las marcas que se están promocionando hoy. Un socio con 'activo: false' queda guardado en
    ajustes.yaml pero el bot lo trata como a cualquier otra empresa: sin prioridad, sin llamado y sin promos."""
    return {
        clave: socio for clave, socio in (cfg.get("socios") or {}).items()
        if isinstance(socio, dict) and ("activo" not in socio or verdadero(socio.get("activo")))
    }


def cargar_ajustes(ruta: Path | None = None) -> dict:
    ruta = ruta or RAIZ / "ajustes.yaml"
    with open(ruta, encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    for clave in ("marca", "telegram", "x", "ritmo", "colores", "ia", "socios", "imagen", "imagen_ia"):
        cfg[clave] = cfg.get(clave) if isinstance(cfg.get(clave), dict) else {}
    for clave in ("categorias", "no_nombrar", "titulos_descartados", "palabras_clave", "fuentes", "promos"):
        cfg[clave] = cfg.get(clave) if isinstance(cfg.get(clave), list) else []
    # Un renglón a medio escribir (un socio sin nada debajo, una promo vacía) no tiene que romper nada.
    cfg["socios"] = {str(k): v for k, v in cfg["socios"].items() if isinstance(v, dict)}
    cfg["promos"] = [p for p in cfg["promos"] if isinstance(p, dict)]
    cfg["fuentes"] = [f for f in cfg["fuentes"] if isinstance(f, dict)]
    return cfg


def zona(cfg: dict) -> ZoneInfo:
    return ZoneInfo(cfg["ritmo"].get("zona_horaria", "America/Argentina/Buenos_Aires"))


def ahora_utc() -> datetime:
    return datetime.now(timezone.utc)


def a_hora(valor) -> time:
    """'10:30' -> las diez y media. (Si en ajustes.yaml la hora quedó sin comillas, YAML la convierte
    en un número de minutos: 10:30 -> 630. También se acepta.)"""
    if isinstance(valor, int) and not isinstance(valor, bool):
        return time(valor // 60 % 24, valor % 60)
    h, m = str(valor).strip().split(":")[:2]
    return time(int(h), int(m))


def _minutos(t: time) -> int:
    return t.hour * 60 + t.minute


def _silencio(cfg: dict) -> tuple[int, int] | None:
    r = cfg["ritmo"]
    if not r.get("silencio_desde") or not r.get("silencio_hasta"):
        return None
    return _minutos(a_hora(r["silencio_desde"])), _minutos(a_hora(r["silencio_hasta"]))


def _minuto_en_silencio(minuto: int, silencio: tuple[int, int] | None) -> bool:
    if silencio is None:
        return False
    desde, hasta = silencio
    if desde <= hasta:
        return desde <= minuto < hasta
    return minuto >= desde or minuto < hasta


def en_silencio(cfg: dict, momento: datetime) -> bool:
    """True si la hora local cae en el horario de silencio (puede cruzar la medianoche)."""
    return _minuto_en_silencio(_minutos(momento.astimezone(zona(cfg)).time()), _silencio(cfg))


def cupo_a_esta_hora(cfg: dict, momento: datetime) -> int:
    """Cuántas publicaciones puede llevar el día a esta hora, para repartirlas parejo
    (y que las 10 del día no salgan todas a la mañana)."""
    tope = int(cfg["ritmo"].get("max_por_dia", 10))
    silencio = _silencio(cfg)
    ahora = _minutos(momento.astimezone(zona(cfg)).time())
    activos_total = sum(1 for m in range(1440) if not _minuto_en_silencio(m, silencio))
    activos_ya = sum(1 for m in range(ahora) if not _minuto_en_silencio(m, silencio))
    if activos_total == 0:
        return tope
    return min(tope, int(tope * activos_ya / activos_total) + 1)


def fecha_corta(momento: datetime, cfg: dict) -> str:
    local = momento.astimezone(zona(cfg))
    return f"{local.day} {MESES[local.month - 1]} {local.year}"


# ----------------------------------------------------------------- textos

def sin_acentos(texto: str) -> str:
    descompuesto = unicodedata.normalize("NFD", texto)
    return "".join(c for c in descompuesto if unicodedata.category(c) != "Mn")


def normalizar(texto: str) -> str:
    """Minúsculas, sin acentos, sin signos: para comparar títulos y buscar palabras."""
    t = sin_acentos(texto or "").lower()
    t = re.sub(r"[^a-z0-9&%$ ]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


_ETIQUETA = re.compile(r"<[^>]+>")
_BLOQUE = re.compile(r"(?is)<(script|style|iframe)\b.*?</\1>")
_RELLENO = [
    re.compile(r"(?i)\bthe post .{0,200}? (appeared|first appeared) (first )?on .{0,80}$"),
    re.compile(r"(?i)\b(leer m[aá]s|seguir leyendo|read more|continue reading|leer)\s*\.{0,3}$"),
    re.compile(r"\[(…|\.\.\.)\]\s*$"),
]


def limpiar_html(texto: str | None, maximo: int = 700) -> str:
    """Saca etiquetas HTML y relleno típico de los feeds. Devuelve texto plano."""
    t = html.unescape(texto or "")
    t = _BLOQUE.sub(" ", t)
    t = _ETIQUETA.sub(" ", t)
    t = html.unescape(t).replace(ESPACIO_DURO, " ")
    t = re.sub(r"\s+", " ", t).strip()
    for patron in _RELLENO:
        t = patron.sub("", t).strip()
    if len(t) > maximo:
        t = t[:maximo].rsplit(" ", 1)[0] + "…"
    return t


def ocultar(texto: str, *secretos: str | None) -> str:
    """Tapa claves y tokens antes de escribir un error en el registro."""
    for s in secretos:
        if s and len(s) >= 6:
            texto = texto.replace(s, "***")
    return re.sub(r"bot\d{6,}:[A-Za-z0-9_-]{20,}", "bot***", texto)
