"""La memoria del bot: qué noticias ya miró, qué publicó y qué quedó en espera.
Se guarda en estado.json (un archivo de texto que el bot actualiza solo)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .filtro import Grupo
from .util import RAIZ, ahora_utc, log, zona

VACIO = {
    "version": 1,
    "evaluadas": {},      # id de noticia -> cuándo la miró la IA
    "publicadas": [],     # historial de publicaciones
    "pendientes": [],     # noticias buenas que esperan su turno
    "fotos_usadas": [],   # para no repetir siempre la misma foto
    "avisos": {},         # último aviso de cada tipo de error
    "variante": 0,        # para ir rotando el diseño de la imagen
    "promos": {},         # id de promo -> cuándo le tocó el turno por última vez
    "promos_hechas": 0,   # contador, para ir cambiando el enfoque del texto
    "charlas": [],        # publicaciones de charla en X (preguntas, dilemas): fecha, formato, texto, id
    "calendario": {},     # "agenda": día de la última agenda · "avisos": evento -> cuándo se avisó
    "grok": {},           # temas del día en X (de Grok) y cuándo se mandaron los posts virales
}


def _fecha_o_nada(valor) -> datetime | None:
    try:
        return datetime.fromisoformat(str(valor))
    except (TypeError, ValueError):
        return None


class Estado:
    def __init__(self, ruta: Path | None = None) -> None:
        self.ruta = ruta or RAIZ / "estado.json"
        self.datos = json.loads(json.dumps(VACIO))
        self.rota = False        # True si había memoria pero no se pudo leer (no se publica a ciegas)
        if self.ruta.exists():
            try:
                leido = json.loads(self.ruta.read_text(encoding="utf-8"))
                if not isinstance(leido, dict):
                    raise ValueError("no es un objeto")
                self.datos.update(leido)
                for clave, vacio in VACIO.items():       # un valor con el tipo cambiado también es memoria rota
                    if not isinstance(self.datos[clave], type(vacio)):
                        raise ValueError(f"'{clave}' tiene un formato inesperado")
                self._emprolijar()
            except (ValueError, OSError) as error:
                log.warning("No pude leer la memoria (%s). Arranco de cero.", error)
                self.datos = json.loads(json.dumps(VACIO))
                self.rota = True

    def _emprolijar(self) -> None:
        """Deja afuera los renglones que no se pueden usar (sin fecha, o con la fecha mal escrita),
        por si alguien tocó el archivo a mano. Una fecha sin zona horaria se toma como UTC."""
        def fecha(valor) -> str | None:
            try:
                d = datetime.fromisoformat(str(valor))
            except ValueError:
                return None
            return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).isoformat()

        publicadas = []
        for p in self.datos["publicadas"]:
            if isinstance(p, dict) and fecha(p.get("fecha")):
                publicadas.append({**p, "fecha": fecha(p["fecha"])})
        self.datos["publicadas"] = publicadas
        for clave in ("avisos", "evaluadas", "promos"):
            self.datos[clave] = {str(k): fecha(v) for k, v in self.datos[clave].items() if fecha(v)}
        self.datos["pendientes"] = [p for p in self.datos["pendientes"] if isinstance(p, dict)]
        self.datos["fotos_usadas"] = [str(f) for f in self.datos["fotos_usadas"] if isinstance(f, str)]

    # ------------------------------------------------------------ guardar
    def guardar(self) -> None:
        self.podar()
        self.ruta.write_text(json.dumps(self.datos, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    def podar(self) -> None:
        limite = (ahora_utc() - timedelta(days=5)).isoformat()
        self.datos["evaluadas"] = {k: v for k, v in self.datos["evaluadas"].items() if v >= limite}
        self.datos["publicadas"] = self.datos["publicadas"][-300:]
        self.datos["fotos_usadas"] = self.datos["fotos_usadas"][-60:]

    # ---------------------------------------------------------- evaluadas
    def fue_evaluada(self, id_noticia: str) -> bool:
        return id_noticia in self.datos["evaluadas"]

    def marcar_evaluadas(self, ids: list[str]) -> None:
        marca = ahora_utc().isoformat()
        for i in ids:
            self.datos["evaluadas"][i] = marca

    # --------------------------------------------------------- publicadas
    def publicadas_hoy(self, cfg: dict, momento: datetime, tipo: str | None = None, con_llamado: bool = False) -> int:
        """Cuántas publicaciones van hoy. Se puede contar solo un tipo ('promo' / 'noticia')
        o solo las que llevaron el llamado a registrarse."""
        hoy = momento.astimezone(zona(cfg)).date()
        total = 0
        for p in self.datos["publicadas"]:
            if datetime.fromisoformat(p["fecha"]).astimezone(zona(cfg)).date() != hoy:
                continue
            if tipo and p.get("tipo", "noticia") != tipo:
                continue
            if con_llamado and not p.get("llamado"):
                continue
            total += 1
        return total

    def ultima_fecha(self, tipo: str) -> datetime | None:
        """Cuándo salió la última publicación de ese tipo ('promo' o 'noticia')."""
        for p in reversed(self.datos["publicadas"]):
            if p.get("tipo", "noticia") == tipo:
                return datetime.fromisoformat(p["fecha"])
        return None

    def minutos_desde_ultima(self, momento: datetime) -> float | None:
        if not self.datos["publicadas"]:
            return None
        ultima = datetime.fromisoformat(self.datos["publicadas"][-1]["fecha"])
        return (momento - ultima).total_seconds() / 60

    def _noticias(self, momento: datetime, horas: float) -> list[dict]:
        """Las noticias publicadas en las últimas horas (las promos no cuentan)."""
        desde = momento - timedelta(hours=horas)
        return [
            p for p in self.datos["publicadas"]
            if p.get("tipo", "noticia") == "noticia" and datetime.fromisoformat(p["fecha"]) >= desde
        ]

    def titulos_recientes(self, momento: datetime, horas: float = 48) -> list[str]:
        """Títulos de lo publicado en los últimos dos días, para no repetir una noticia. No se mira
        más atrás: 'el BCRA compró dólares' de la semana pasada no tiene que tapar la de hoy."""
        salida: list[str] = []
        for p in self._noticias(momento, horas):
            salida.append(p.get("titular", ""))
            salida.extend(p.get("titulos_originales", [])[:3])
        return [t for t in salida if t]

    def titulares_recientes(self, momento: datetime, horas: float = 48, cantidad: int = 15) -> list[str]:
        return [p.get("titular", "") for p in self._noticias(momento, horas)[-cantidad:] if p.get("titular")]

    def charlas_hoy(self, cfg: dict, momento: datetime, ensenanza: bool = False) -> int:
        """Cuántas charlas de X (o, con ensenanza=True, cuántas enseñanzas) salieron hoy."""
        hoy = momento.astimezone(zona(cfg)).date()
        return sum(1 for c in self.datos["charlas"]
                   if isinstance(c, dict) and _fecha_o_nada(c.get("fecha")) and
                   _fecha_o_nada(c.get("fecha")).astimezone(zona(cfg)).date() == hoy
                   and (c.get("formato") == "ensenanza") == ensenanza)

    def charlas_recientes(self, cantidad: int = 30) -> list[str]:
        return [str(c.get("texto", "")) for c in self.datos["charlas"][-cantidad:] if isinstance(c, dict) and c.get("texto")]

    def agregar_charla(self, registro: dict) -> None:
        self.datos["charlas"] = [c for c in self.datos["charlas"] if isinstance(c, dict)][-200:] + [registro]

    def agregar_publicada(self, registro: dict) -> None:
        self.datos["publicadas"].append(registro)

    # --------------------------------------------------------- pendientes
    def pendientes(self, cfg: dict, momento: datetime) -> list[Grupo]:
        """Noticias en espera que todavía no vencieron."""
        general = float(cfg["ritmo"].get("antiguedad_maxima_horas", 6))
        vigentes: list[Grupo] = []
        for d in self.datos["pendientes"]:
            try:
                g = Grupo.de_dict(d)
                vigente = g.fecha >= momento - timedelta(hours=g.horas or general) and g.intentos < 2
            except (KeyError, TypeError, ValueError, AttributeError):
                continue                     # un renglón que no se puede leer se deja de lado
            if vigente:
                vigentes.append(g)
        return vigentes

    def guardar_pendientes(self, grupos: list[Grupo]) -> None:
        self.datos["pendientes"] = [g.a_dict() for g in grupos[:8]]

    # -------------------------------------------------------------- fotos
    def foto_usada(self, titulo: str) -> bool:
        return titulo in self.datos["fotos_usadas"]

    def marcar_foto(self, titulo: str) -> None:
        if titulo:
            self.datos["fotos_usadas"].append(titulo)

    def proxima_variante(self) -> int:
        self.datos["variante"] = int(self.datos.get("variante", 0)) + 1
        return self.datos["variante"]

    # ------------------------------------------------------------- promos
    def promo_que_toca(self, activas: list[dict]) -> dict | None:
        """La promo que hace más tiempo que no sale (las que nunca salieron van primero)."""
        if not activas:
            return None
        return min(activas, key=lambda p: self.datos["promos"].get(str(p.get("id")), ""))

    def marcar_promo(self, id_promo: str, momento: datetime) -> None:
        """Anota que a esa promo ya le tocó el turno (salió, o se intentó y no pasó los controles):
        pasa al final de la fila y la próxima usa otro enfoque de texto."""
        self.datos["promos"][str(id_promo)] = momento.isoformat()
        self.datos["promos_hechas"] = int(self.datos.get("promos_hechas", 0)) + 1

    # ------------------------------------------------------------- avisos
    def puede_avisar(self, clave: str, horas: float = 12) -> bool:
        ultimo = self.datos["avisos"].get(clave)
        if ultimo and ahora_utc() - datetime.fromisoformat(ultimo) < timedelta(hours=horas):
            return False
        self.datos["avisos"][clave] = ahora_utc().isoformat()
        return True
