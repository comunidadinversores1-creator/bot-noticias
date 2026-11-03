"""El robot de los carruseles (el personaje de Eze), dibujado en SVG: gratis, siempre igual y en varias poses.

Uso: svg("pensando") -> el dibujo listo para meter en el HTML de una lámina."""
from __future__ import annotations

K = 'stroke="#111" stroke-width="9" stroke-linejoin="round" stroke-linecap="round"'
VERDE, VERDE_CLARO, ROJO, AMARILLO, CREMA = "#22c55e", "#4ade80", "#ef4444", "#facc15", "#FBF6E9"

POSES = {
    "saludo": "saluda con la mano (portada, bienvenida, cierre)",
    "pensando": "se pregunta algo, con un signo de pregunta (preguntas, dudas, mitos)",
    "lupa": "mira con una lupa (analizar, investigar, detectar estafas)",
    "festejo": "festeja con monedas (logros, ganancias posibles, interés compuesto)",
    "alerta": "preocupado, con un cartel de peligro y el gráfico bajando (errores, riesgos, caídas)",
    "celular": "muestra un celular con un gráfico (apps, exchanges, operar)",
    "escudo": "se protege con un escudo (stop loss, seguridad, gestión de riesgo)",
    "dinero": "sostiene un billete (ahorro, sueldo, dinero del día a día)",
}


def _brazo(desde: tuple[int, int], control: tuple[int, int], hasta: tuple[int, int]) -> str:
    return (f'<path d="M{desde[0]} {desde[1]} Q{control[0]} {control[1]} {hasta[0]} {hasta[1]}" fill="none" '
            f'stroke="#111" stroke-width="28" stroke-linecap="round"/><circle cx="{hasta[0]}" cy="{hasta[1]}" r="30" fill="{VERDE}" {K}/>')


def _rayitos(derecha: bool = True) -> str:
    izq = f'<line x1="80" y1="80" x2="130" y2="110" {K}/><line x1="60" y1="160" x2="118" y2="162" {K}/>'
    der = f'<line x1="680" y1="80" x2="630" y2="110" {K}/><line x1="700" y1="160" x2="642" y2="162" {K}/>' if derecha else ""
    return f"<g>{izq}{der}</g>"


def _cuerpo(baja: bool = False) -> str:
    linea = ("270,150 320,185 360,165 410,230 450,205 495,265" if baja else "270,250 320,215 360,235 410,170 450,195 495,135")
    punta = (495, 265) if baja else (495, 135)
    color = ROJO if baja else VERDE_CLARO
    return (f'<ellipse cx="380" cy="615" rx="200" ry="20" fill="#111" opacity=".12"/>'
            f'<line x1="380" y1="70" x2="380" y2="22" {K}/><circle cx="380" cy="22" r="18" fill="{VERDE}" {K}/>'
            f'<rect x="210" y="70" width="340" height="280" rx="40" fill="{VERDE}" {K}/>'
            f'<rect x="245" y="105" width="270" height="190" rx="24" fill="#111" {K}/>'
            f'<polyline points="{linea}" fill="none" stroke="{color}" stroke-width="10" stroke-linecap="round" stroke-linejoin="round"/>'
            f'<circle cx="{punta[0]}" cy="{punta[1]}" r="10" fill="{color}"/>'
            f'<rect x="170" y="170" width="40" height="80" rx="14" fill="#111"/><rect x="550" y="170" width="40" height="80" rx="14" fill="#111"/>'
            f'<rect x="255" y="365" width="250" height="200" rx="34" fill="#111" {K}/><rect x="300" y="400" width="160" height="70" rx="14" fill="{VERDE}"/>'
            f'<circle cx="335" cy="510" r="14" fill="{VERDE}"/><circle cx="380" cy="510" r="14" fill="{AMARILLO}"/><circle cx="425" cy="510" r="14" fill="{ROJO}"/>')


def _moneda(x: int, y: int, r: int = 34) -> str:
    return (f'<circle cx="{x}" cy="{y}" r="{r}" fill="{AMARILLO}" {K}/>'
            f'<text x="{x}" y="{y + r * 0.42:.0f}" text-anchor="middle" font-family="Luck, Arial Black" font-size="{r * 1.2:.0f}" fill="#111">$</text>')


def svg(pose: str, ancho: int = 760) -> str:
    pose = pose if pose in POSES else "saludo"
    izq_abajo = _brazo((255, 410), (170, 420), (160, 500))
    der_abajo = _brazo((505, 410), (590, 420), (600, 500))
    extra, brazos, baja = "", "", False
    if pose == "saludo":
        brazos = izq_abajo + _brazo((505, 410), (600, 380), (620, 300))
        extra = (f'<path d="M650 250 Q672 232 668 210" fill="none" {K}/><path d="M670 285 Q700 275 705 250" fill="none" {K}/>')
    elif pose == "pensando":
        brazos = izq_abajo + _brazo((505, 410), (610, 440), (560, 352))
        extra = (f'<text x="655" y="215" text-anchor="middle" font-family="Luck, Arial Black" font-size="190" fill="{ROJO}" '
                 f'stroke="#111" stroke-width="7" paint-order="stroke">?</text>')
    elif pose == "lupa":
        brazos = izq_abajo + _brazo((505, 410), (600, 430), (618, 360))
        extra = (f'<line x1="630" y1="345" x2="665" y2="300" stroke="#111" stroke-width="22" stroke-linecap="round"/>'
                 f'<circle cx="690" cy="255" r="62" fill="#e0f2fe" fill-opacity=".85" {K}/>'
                 f'<path d="M660 230 Q672 212 694 210" fill="none" stroke="#fff" stroke-width="10" stroke-linecap="round"/>')
    elif pose == "festejo":
        brazos = _brazo((255, 410), (180, 350), (175, 255)) + _brazo((505, 410), (580, 350), (585, 255))
        extra = _moneda(95, 330) + _moneda(668, 360, 30) + _moneda(120, 470, 26) + _moneda(650, 480, 24)
    elif pose == "alerta":
        baja = True
        brazos = _brazo((255, 410), (150, 340), (205, 270)) + _brazo((505, 410), (610, 340), (555, 270))
        extra = (f'<path d="M650 120 L725 250 L575 250 Z" fill="{AMARILLO}" {K}/>'
                 f'<line x1="650" y1="160" x2="650" y2="205" stroke="#111" stroke-width="14" stroke-linecap="round"/>'
                 f'<circle cx="650" cy="230" r="8" fill="#111"/>'
                 )
    elif pose == "celular":
        brazos = izq_abajo + _brazo((505, 410), (600, 450), (600, 470))
        extra = (f'<g transform="rotate(12 640 430)"><rect x="590" y="330" width="120" height="200" rx="22" fill="#111"/>'
                 f'<rect x="604" y="348" width="92" height="160" rx="10" fill="{CREMA}"/>'
                 f'<polyline points="614,480 636,455 654,468 680,410" fill="none" stroke="#16a34a" stroke-width="9" stroke-linecap="round" stroke-linejoin="round"/></g>'
                 f'<circle cx="600" cy="470" r="30" fill="{VERDE}" {K}/>')
    elif pose == "escudo":
        brazos = _brazo((255, 410), (190, 450), (175, 440)) + der_abajo
        extra = (f'<path d="M150 330 L250 360 L250 450 Q250 520 150 560 Q50 520 50 450 L50 360 Z" fill="{VERDE}" {K}/>'
                 f'<path d="M105 450 L140 485 L200 410" fill="none" stroke="#111" stroke-width="14" stroke-linecap="round" stroke-linejoin="round"/>')
    elif pose == "dinero":
        brazos = izq_abajo + _brazo((505, 410), (600, 440), (605, 460))
        extra = (f'<g transform="rotate(-14 650 440)"><rect x="565" y="395" width="190" height="100" rx="12" fill="#86efac" {K}/>'
                 f'<circle cx="660" cy="445" r="30" fill="none" stroke="#111" stroke-width="7"/>'
                 f'<text x="660" y="461" text-anchor="middle" font-family="Luck, Arial Black" font-size="44" fill="#111">$</text></g>'
                 f'<circle cx="605" cy="460" r="30" fill="{VERDE}" {K}/>')
    alto = round(ancho * 640 / 760)
    return (f'<svg width="{ancho}" height="{alto}" viewBox="0 0 760 640" xmlns="http://www.w3.org/2000/svg">'
            f'{_rayitos(pose not in ("pensando", "alerta", "lupa"))}{_cuerpo(baja)}{brazos}{extra}</svg>')
