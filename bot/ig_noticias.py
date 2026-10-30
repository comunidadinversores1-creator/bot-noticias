"""Carruseles de noticias para Instagram: el de una noticia fuerte (@ezebagnato) y el resumen de la semana
(@comunidad_inversores_ok). Solo arman las láminas y el texto del post con datos que YA pasaron el control
de las noticias (nada se inventa acá)."""
from __future__ import annotations

import io
import re

from . import carrusel_html as ch
from .filtro import fichas, parecido


def _limpio(texto: str, largo: int) -> str:
    texto = re.sub(r"\s+", " ", str(texto or "")).strip().replace("*", "")
    if len(texto) <= largo:
        return texto
    corte = texto[:largo].rsplit(" ", 1)[0]
    return corte.rstrip(",;:") + "…"


def _resaltar(titulo: str) -> str:
    """Marca con asteriscos las primeras 2 palabras del titular (se pintan de color)."""
    palabras = _limpio(titulo, 62).split()
    if len(palabras) <= 3:
        return " ".join(palabras)
    return "*" + " ".join(palabras[:2]) + "* " + " ".join(palabras[2:])


def carrusel_noticia(post: dict, urgente: bool, foto: bytes | None, logo: bytes | None, credito: str = "",
                     usuario: str = "@ezebagnato") -> tuple[list[bytes], str]:
    """(láminas, texto del post) de una noticia fuerte. 'post' trae titular, resumen, dato_clave, por_que_importa, pregunta, fuente."""
    laminas = [{"tipo": "portada", "etiqueta": "Última hora" if urgente else "Noticia clave",
                "titulo": _resaltar(post.get("titular_imagen") or post.get("titular", "")), "bajada": _limpio(post.get("dato_clave"), 80)}]
    pasos = [("Qué pasó", "Lo que se sabe", post.get("resumen")),
             ("El dato", "El número clave", post.get("dato_clave")),
             ("Por qué importa", "Por qué te importa", post.get("por_que_importa")),
             ("Y vos", "¿Qué pensás?", post.get("pregunta"))]
    for etiqueta, titulo, texto in pasos:
        texto = _limpio(texto, 260)
        if texto:
            laminas.append({"tipo": "paso", "etiqueta": etiqueta, "titulo": titulo, "texto": texto, "grande": True})
    ch.usar_diseno("rojo·diagonal" if urgente else "naranja·liso")
    try:
        jpgs = ch.sacar_fotos(ch.titular(laminas, "", False, usuario, foto=foto, logo=logo, credito=credito))
    finally:
        ch.usar_diseno(None)
    texto = (f"{'🚨' if urgente else '📰'} {_limpio(post.get('titular'), 120)}\n\n{_limpio(post.get('resumen'), 330)}\n\n"
             + (f"👇 {_limpio(post.get('pregunta'), 120)}\n\n" if post.get("pregunta") else "")
             + "📌 Guardalo\n✈️ Mandáselo a alguien que invierta\n🔔 Seguime para enterarte primero"
             + (f"\n\nFuente: {post['fuente']}" if post.get("fuente") else ""))
    return jpgs, texto


def semana_top(publicadas: list[dict], desde, hasta, cantidad: int = 5) -> list[dict]:
    """Las noticias más importantes de la semana, sin repetir el mismo tema (un hecho con 5 novedades cuenta una vez)."""
    from datetime import datetime
    candidatas = []
    for p in publicadas:
        if p.get("tipo") != "noticia" or not p.get("titular"):
            continue
        try:
            fecha = datetime.fromisoformat(p["fecha"])
        except (KeyError, ValueError):
            continue
        if desde <= fecha <= hasta:
            candidatas.append(p)
    candidatas.sort(key=lambda p: (-int(p.get("impacto") or 0), p["fecha"]))
    elegidas: list[dict] = []
    for p in candidatas:
        f = fichas(p["titular"])
        if any(parecido(f, fichas(q["titular"])) >= 0.34 or _mismo_nombre(p, q) for q in elegidas):
            continue
        elegidas.append(p)
        if len(elegidas) >= cantidad:
            break
    return elegidas


def _mismo_nombre(a: dict, b: dict) -> bool:
    """Dos titulares que arrancan con el mismo nombre propio (ej. 'Binance ...') son el mismo tema de la semana."""
    pa, pb = a["titular"].split(), b["titular"].split()
    return bool(pa and pb and len(pa[0]) > 3 and pa[0].lower() == pb[0].lower() and a.get("categoria") == b.get("categoria"))


def carrusel_semana(noticias: list[dict], fecha_texto: str, usuario: str = "@comunidad_inversores_ok") -> tuple[list[bytes], str]:
    laminas = [{"tipo": "portada", "etiqueta": "Resumen semanal", "titulo": "Lo que *movió* el mercado",
                "bajada": f"{len(noticias)} noticias que importaron · {fecha_texto}"}]
    for i, n in enumerate(noticias, 1):
        laminas.append({"tipo": "paso", "etiqueta": f"Noticia {i}" + (f" · {n['categoria']}" if n.get("categoria") else ""),
                        "titulo": _resaltar(n["titular"]), "grande": True,
                        "texto": _limpio(n.get("resumen") or (f"Fuente: {n['fuente']}" if n.get("fuente") else ""), 240) or "Una de las que más movió a los mercados esta semana."})
    ch.usar_diseno("celeste·rejilla")
    try:
        jpgs = ch.sacar_fotos(ch.titular(laminas, "", False, usuario))
    finally:
        ch.usar_diseno(None)
    numeros = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣"]
    lista = "\n".join(f"{numeros[i]} {_limpio(n['titular'], 110)}" for i, n in enumerate(noticias))
    texto = (f"📰 Lo que movió el mercado esta semana ({fecha_texto})\n\n{lista}\n\n"
             "📌 Guardalo para repasarlo\n🔔 Seguinos para no perderte ninguna")
    return jpgs, texto
