"""Arma placas de muestra con todos los diseños, sin internet.

Uso:  python -m tests.muestra_placas CARPETA_SALIDA [CARPETA_CON_FOTOS]
Si no se pasa una carpeta con fotos, usa un fondo de relleno (solo sirve para revisar el diseño)."""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

from bot.imagen import Foto, armar_placa
from bot.util import cargar_ajustes

MUESTRAS = [
    dict(titular_imagen="La SEC propone cómo deben custodiar cripto los fondos", categoria="REGULACIÓN", tono="alcista", fuente="SEC"),
    dict(titular_imagen="El petróleo salta tras un nuevo ataque a un buque cisterna", categoria="GEOPOLÍTICA", tono="bajista", fuente="The Wall Street Journal"),
    dict(titular_imagen="Bitcoin corrige a USD 83.000 y divide a los analistas", categoria="CRIPTO", tono="bajista", fuente="CriptoNoticias"),
    dict(titular_imagen="El riesgo país se mantiene cerca de los 580 puntos", categoria="ARGENTINA", tono="neutral", fuente="Ámbito"),
    dict(titular_imagen="La Fed baja la tasa 0,25 puntos", categoria="ECONOMÍA", tono="alcista", fuente="Reserva Federal (Fed)"),
    dict(titular_imagen="JPMorgan calcula que entraron USD 50.000 millones a cripto en lo que va del año", categoria="MERCADOS", tono="alcista", fuente="The Block"),
]


def _relleno() -> Image.Image:
    img = Image.new("RGB", (1600, 1000))
    px = img.load()
    for y in range(img.height):
        for x in range(0, img.width):
            px[x, y] = (40 + x * 90 // img.width, 70 + y * 80 // img.height, 110 + (x + y) * 60 // (img.width + img.height))
    return img


def main() -> None:
    salida = Path(sys.argv[1] if len(sys.argv) > 1 else "salida_muestra")
    salida.mkdir(parents=True, exist_ok=True)
    fotos = sorted(Path(sys.argv[2]).glob("*")) if len(sys.argv) > 2 else []
    cfg = cargar_ajustes()
    momento = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)
    n = 0
    for i, muestra in enumerate(MUESTRAS):
        post = dict(muestra)
        imagen = Image.open(fotos[i % len(fotos)]).convert("RGB") if fotos else _relleno()
        foto = Foto("File:Prueba.jpg", "", imagen.width, imagen.height, "image/jpeg", "CC BY-SA 4.0", "Nombre De Autor", "", "2025", imagen=imagen)
        for nombre, f, variante, urgente in (
            ("completa", foto, 0, i == 1),
            ("panel", foto, 1, i == 1),
            ("texto", None, 0, i == 1),
        ):
            n += 1
            (salida / f"{i + 1}_{nombre}.jpg").write_bytes(armar_placa(post, f, cfg, momento, variante, urgente))
    print(f"{n} placas en {salida}")


if __name__ == "__main__":
    main()
