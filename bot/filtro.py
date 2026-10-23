"""Junta la misma noticia contada por varios medios, descarta lo que no sirve
y pone un primer puntaje (después la IA decide el impacto real)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .fuentes import Noticia
from .util import log, normalizar, sin_acentos, socios_activos

VACIAS = set(
    """
    el la los las un una unos unas de del al a ante bajo con contra desde en entre hacia hasta para por
    segun sin sobre tras y o u e ni que se su sus lo le les es son fue ser era han ha hay esta este esto
    estos estas como mas pero ya no si tambien muy cuando donde quien cual cuanto tras durante mientras
    hoy ayer manana este esta the of to in on for and or a an at by with from as is are was were be been
    it its this that these those will would could should has have had not but after over into amid than
    new says say said after before about more its their his her our your you we they he she per via
    """.split()
)

# Palabras que indican que la noticia es MALA para la empresa nombrada: ahí nunca va el
# "registrate con mi link". Es a propósito una lista amplia: si se pasa de cautelosa, lo único
# que ocurre es que una noticia sale sin el llamado. (Se busca en minúsculas y sin acentos.)
MALA_NOTICIA = re.compile(
    r"\b(?:"
    r"hack\w*|ciberataq\w*|cyberattack\w*|ataqu\w*|atac\w*|attack\w*|exploit\w*|breach\w*|vulnerab\w*|phishing|drain\w*|"
    r"rob(?:o|os|a|an|aron|ar|aban|ad[oa]s?)|stolen|stole|theft|sustra\w*|"
    r"multa\w*|fined|fine\s+(?:of|against|for)|penalt\w*|sancion\w*|sanction\w*|"
    r"demand\w*|lawsuit\w*|sued|sues|suing|denunci\w*|querell\w*|acus\w*|imput\w*|charges?|charged|indict\w*|"
    r"culpable\w*|guilty|conden\w*|convict\w*|sentenc\w*|carcel|prision|jail|prison|"
    r"arrest\w*|deten(?:ido|ida|idos|idas|cion|ciones)|detuv\w*|detained|"
    r"allan\w*|raid\w*|embarg\w*|incaut\w*|seiz\w*|clausur\w*|"
    r"fraud\w*|estaf\w*|scam\w*|ponzi|lavado|laundering|"
    r"quiebr\w*|quebr\w*|bancarrota|bankrupt\w*|insolven\w*|"
    r"cierr\w*|cerr(?:o|ara|aran|ar|ando|aron|ado|ada)|clos(?:e[sd]?|ing|ure)|shut\w*|exit(?:s|ed|ing)?|"
    r"se\s+va\s+de|abandon\w*|deja\w*\s+de\s+operar|ceas\w*|"
    r"pierde\w*|perd(?:io|ieron|era|eran|ida|idas)|loss(?:es)?|lost|"
    r"revoc\w*|suspend\w*|paus\w*|halt\w*|congel\w*|freez\w*|froze\w*|bloque\w*|block(?:s|ed|ing)?|"
    r"prohib\w*|ban(?:s|ned|ning)?|veto\w*|delist\w*|deslist\w*|"
    r"caid[ao]s?|cayo|cae|caen|outage\w*|down|falla\w*|fallo|crash\w*|colaps\w*|collaps\w*|crisis|"
    r"despid\w*|layoff\w*|"
    r"investig\w*|probes|probed|probe\s+(?:into|of)|advertenc\w*|warning\w*|alerta\s+(?:de|del|contra)|"
    r"fuga|filtr\w*|leak\w*|retiros\s+masivos|corrida|quej\w*|reclam\w*"
    r")\b"
)
_CIFRA = re.compile(r"\d+(?:[.,]\d+)*")


def fichas(texto: str) -> frozenset[str]:
    """Palabras significativas de un título, para comparar dos títulos."""
    palabras = normalizar(texto).split()
    return frozenset(p for p in palabras if (len(p) > 2 or p.isdigit()) and p not in VACIAS)


def parecido(a: frozenset[str], b: frozenset[str]) -> float:
    """0 = nada que ver, 1 = el mismo título."""
    if not a or not b:
        return 0.0
    comunes = len(a & b)
    if comunes == 0:
        return 0.0
    jaccard = comunes / len(a | b)
    contenido = comunes / min(len(a), len(b)) if comunes >= 4 else 0.0
    return max(jaccard, 0.8 * contenido)


@dataclass
class Grupo:
    """La misma noticia, contada por uno o más medios."""
    principal: Noticia
    miembros: list[Noticia] = field(default_factory=list)
    impacto: int = 0
    categoria: str = ""
    urgente: bool = False
    intentos: int = 0
    socio: str = ""              # 'exness', 'binance'... si la noticia es sobre una marca con la que trabaja Eze

    def __post_init__(self) -> None:
        if not self.miembros:
            self.miembros = [self.principal]
        self._fichas = fichas(self.principal.titulo)

    @property
    def id(self) -> str:
        return self.principal.id

    @property
    def fuentes(self) -> list[str]:
        vistos: list[str] = []
        for m in self.miembros:
            if m.fuente not in vistos:
                vistos.append(m.fuente)
        return vistos

    @property
    def cobertura(self) -> int:
        return max(len(self.fuentes), max(m.cobertura for m in self.miembros))

    @property
    def fecha(self) -> datetime:
        return max(m.fecha for m in self.miembros)

    @property
    def horas(self) -> float:
        return max(m.horas for m in self.miembros)

    def titulos(self) -> str:
        return " | ".join(m.titulo for m in self.miembros)

    def texto_fuente(self, maximo: int = 2600, con_medios: bool = True) -> str:
        """Todo lo que dijeron los medios: es lo ÚNICO que la IA puede usar como dato."""
        partes: list[str] = []
        for m in self.miembros[:6]:
            bloque = f"[{m.fuente}] {m.titulo}" if con_medios else m.titulo
            if m.resumen:
                bloque += f" — {m.resumen}"
            partes.append(bloque)
        return "\n".join(partes)[:maximo]

    def texto_para_cifras(self) -> str:
        """Lo mismo pero sin el nombre de cada medio: el '24' de 'France 24' no es un dato de la noticia."""
        return self.texto_fuente(con_medios=False)

    def a_dict(self) -> dict:
        return {
            "principal": self.principal.a_dict(),
            "miembros": [m.a_dict() for m in self.miembros],
            "impacto": self.impacto,
            "categoria": self.categoria,
            "urgente": self.urgente,
            "intentos": self.intentos,
            "socio": self.socio,
        }

    @classmethod
    def de_dict(cls, d: dict) -> "Grupo":
        return cls(
            principal=Noticia.de_dict(d["principal"]),
            miembros=[Noticia.de_dict(m) for m in d.get("miembros", [])],
            impacto=int(d.get("impacto", 0)),
            categoria=d.get("categoria", ""),
            urgente=bool(d.get("urgente", False)),
            intentos=int(d.get("intentos", 0)),
            socio=d.get("socio", ""),
        )


def agrupar(noticias: list[Noticia], umbral: float = 0.5) -> list[Grupo]:
    """Une las noticias que cuentan lo mismo. Queda como principal la del medio de más peso."""
    ordenadas = sorted(noticias, key=lambda n: (-n.peso, -len(n.resumen), -n.fecha.timestamp()))
    grupos: list[Grupo] = []
    for n in ordenadas:
        f = fichas(n.titulo)
        destino = None
        for g in grupos:
            if g.principal.id == n.id or parecido(f, g._fichas) >= umbral:
                destino = g
                break
        if destino is None:
            grupos.append(Grupo(principal=n))
        elif all(m.id != n.id for m in destino.miembros):
            destino.miembros.append(n)
    return grupos


# ------------------------------------------------------------ nombres

def _aparece(nombre: str, texto_plano: str) -> bool:
    """¿Está ese nombre como palabra entera? Los muy cortos (XM, FBS) respetan mayúsculas."""
    nombre = str(nombre).strip()
    if not nombre:
        return False
    patron = r"(?<![\w])" + re.escape(sin_acentos(nombre)) + r"(?![\w])"
    return bool(re.search(patron, texto_plano, 0 if len(nombre) <= 4 else re.IGNORECASE))


def nombra_prohibido(texto: str, lista: list[str]) -> str | None:
    """Devuelve el nombre de la lista 'no_nombrar' que aparece en el texto, o None."""
    plano = sin_acentos(texto)
    for nombre in lista:
        if _aparece(nombre, plano):
            return str(nombre).strip()
    return None


def detectar_socio(titulos: str, cfg: dict) -> str:
    """Si el título nombra a una de las marcas con las que trabaja Eze, devuelve cuál."""
    plano = sin_acentos(titulos)
    for clave, socio in socios_activos(cfg).items():
        alias = socio.get("alias") or []
        nombres = (list(alias) if isinstance(alias, list) else [alias]) + [socio.get("nombre", "")]
        if any(_aparece(n, plano) for n in nombres):
            return clave
    return ""


def es_mala_noticia(texto: str) -> bool:
    return bool(MALA_NOTICIA.search(sin_acentos(texto or "").lower()))


def titulo_descartado(titulo: str, patrones: list[str]) -> bool:
    plano = normalizar(titulo)
    for p in patrones:
        try:
            if re.search(p, plano):
                return True
        except re.error:
            log.warning("Patrón mal escrito en titulos_descartados: %s", p)
    return False


def puntaje(g: Grupo, cfg: dict, ahora: datetime) -> float:
    """Primer orden, sin IA: medio confiable + varios medios + palabras fuertes + recién salida."""
    p = g.principal.peso
    p += min(len(g.fuentes) - 1, 3) * 1.5
    p += min(max(m.cobertura for m in g.miembros) - 1, 4) * 0.4
    texto = " " + normalizar(g.principal.titulo + " " + g.principal.resumen) + " "
    aciertos = sum(1 for clave in cfg["palabras_clave"] if f" {normalizar(str(clave))} " in texto)
    p += min(aciertos, 4) * 0.8
    horas = max(0.0, (ahora - g.fecha).total_seconds() / 3600)
    p += max(0.0, 1.5 - horas * 0.25)
    if g.principal.titulo.rstrip().endswith("?"):
        p -= 1.0
    if g.socio:
        p += 3.0                 # las marcas con las que trabaja Eze siempre llegan a la IA
    return round(p, 2)


def preparar(noticias: list[Noticia], cfg: dict, ahora: datetime) -> tuple[list[Grupo], dict]:
    """De todas las noticias crudas a la lista de candidatas ordenadas por puntaje."""
    general = float(cfg["ritmo"].get("antiguedad_maxima_horas", 6))
    cuenta = {"total": len(noticias), "viejas": 0, "descartadas": 0, "prohibidas": 0}
    frescas: list[Noticia] = []
    for n in noticias:
        if n.fecha < ahora - timedelta(hours=n.horas or general):
            cuenta["viejas"] += 1
        elif titulo_descartado(n.titulo, cfg["titulos_descartados"]):
            cuenta["descartadas"] += 1
        else:
            frescas.append(n)
    grupos = agrupar(frescas)
    limpios: list[Grupo] = []
    for g in grupos:
        # Si CUALQUIER medio del grupo nombra a una empresa de la lista, se saltea toda la noticia.
        texto = " ".join(m.titulo + " " + m.resumen for m in g.miembros)
        nombre = nombra_prohibido(texto, cfg["no_nombrar"])
        if nombre:
            cuenta["prohibidas"] += 1
            log.info("  salteada por nombrar a %s: %s", nombre, g.principal.titulo[:80])
            continue
        g.socio = detectar_socio(g.titulos(), cfg)
        limpios.append(g)
    limpios.sort(key=lambda g: -puntaje(g, cfg, ahora))
    cuenta["grupos"] = len(limpios)
    return limpios, cuenta


def _cifras(titulo: str) -> set[float]:
    return {float(re.sub(r"[.,]", "", n)) for n in _CIFRA.findall(titulo or "")}


def _mismas_cifras(a: set[float], b: set[float]) -> bool:
    """¿Las cifras del título que trae menos aparecen (con un margen por redondeo) en el otro?
    'roban USD 351 millones' y 'roban más de 350 millones' sí; 'compró 150' y 'compró 80' no."""
    chico, grande = (a, b) if len(a) <= len(b) else (b, a)
    return all(any(abs(x - y) <= 0.02 * max(x, y) for y in grande) for x in chico)


def ya_contada(g: Grupo, titulos_publicados: list[str], umbral: float = 0.5) -> bool:
    """True si ya se publicó algo que cuenta lo mismo.

    Dos títulos casi iguales pero con cifras distintas ("el BCRA compró USD 150 millones" ayer,
    "el BCRA compró USD 80 millones" hoy) NO se dan por repetidos acá: es la noticia de otro día
    o una novedad, y eso lo termina de decidir la IA, que ve la lista de lo ya publicado."""
    propias = [(fichas(m.titulo), _cifras(m.titulo)) for m in g.miembros]
    for t in titulos_publicados:
        ft, ct = fichas(t), _cifras(t)
        for f, c in propias:
            if parecido(f, ft) >= umbral and (not (c and ct) or _mismas_cifras(c, ct)):
                return True
    return False
