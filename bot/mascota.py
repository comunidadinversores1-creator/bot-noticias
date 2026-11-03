"""El personaje de los carruseles (el robot verde de Eze), dibujado por la IA de imágenes de Google en distintas poses.

Estilo fijo: dibujo animado plano, contorno negro grueso, fondo crema liso, sin texto."""
from __future__ import annotations

import base64
import logging

import requests

from .imagen import INTERACCIONES, _primera_imagen

log = logging.getLogger("bot")

PERSONAJE = (
    "A cute friendly cartoon robot mascot: square green head (#22c55e) with a big black screen as its face showing a "
    "rising green line chart, a short antenna with a small green ball on top, a black rectangular body with a green "
    "panel and three small lights (green, yellow, red), black tube arms with green round hands."
)
ESTILO = (
    "Flat 2D cartoon sticker illustration, thick black outlines, simple shapes, bold flat colors (green, black, cream, "
    "small touches of red and yellow), a few small black motion lines around it, plain solid cream background (#FBF6E9). "
    "No text, no letters, no numbers, no logos, no watermark, no frame."
)
MODELOS = ["gemini-3.1-flash-lite-image", "gemini-2.5-flash-image", "gemini-3-pro-image-preview"]


def _por_interacciones(pedido: str, clave: str, modelo: str) -> tuple[bytes | None, str]:
    r = requests.post(INTERACCIONES, headers={"x-goog-api-key": clave, "Content-Type": "application/json"},
                      json={"model": modelo, "input": [{"type": "text", "text": pedido}],
                            "response_format": {"type": "image", "mime_type": "image/jpeg", "aspect_ratio": "1:1", "image_size": "1K"}},
                      timeout=120)
    if r.status_code != 200:
        return None, f"interactions {r.status_code}: {r.text[:160]}"
    dato = _primera_imagen(r.json())
    return (base64.b64decode(dato), "ok") if dato else (None, "interactions: sin imagen")


def _por_generate(pedido: str, clave: str, modelo: str) -> tuple[bytes | None, str]:
    r = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent",
                      headers={"x-goog-api-key": clave, "Content-Type": "application/json"},
                      json={"contents": [{"parts": [{"text": pedido}]}],
                            "generationConfig": {"responseModalities": ["IMAGE"], "imageConfig": {"aspectRatio": "1:1"}}},
                      timeout=120)
    if r.status_code != 200:
        return None, f"generateContent {r.status_code}: {r.text[:160]}"
    for c in r.json().get("candidates") or []:
        for p in (c.get("content") or {}).get("parts") or []:
            d = p.get("inlineData") or p.get("inline_data") or {}
            if d.get("data"):
                return base64.b64decode(d["data"]), "ok"
    return None, "generateContent: sin imagen"


def _por_openai(pedido: str, clave: str, modelo: str) -> tuple[bytes | None, str]:
    """La misma IA de imágenes que usa ChatGPT (necesita una clave de la API de OpenAI con saldo)."""
    r = requests.post("https://api.openai.com/v1/images/generations",
                      headers={"Authorization": f"Bearer {clave}", "Content-Type": "application/json"},
                      json={"model": modelo, "prompt": pedido, "size": "1024x1024", "quality": "medium", "n": 1},
                      timeout=180)
    if r.status_code != 200:
        return None, f"openai {modelo} {r.status_code}: {r.text[:200]}"
    for d in r.json().get("data") or []:
        if d.get("b64_json"):
            return base64.b64decode(d["b64_json"]), "ok"
    return None, f"openai {modelo}: sin imagen"


def dibujar(pose: str, clave: str, modelos: list[str] | None = None, clave_openai: str = "") -> tuple[bytes | None, str]:
    """(png, informe). Primero la IA de imágenes de ChatGPT (si hay clave), después las de Google."""
    pedido = f"{PERSONAJE} Pose: {pose}. {ESTILO}"
    informe = []
    if clave_openai:
        for modelo in ("gpt-image-1", "gpt-image-1-mini"):
            try:
                img, nota = _por_openai(pedido, clave_openai, modelo)
            except requests.RequestException as error:
                img, nota = None, f"openai {modelo}: {type(error).__name__}"
            informe.append(nota)
            if img:
                return img, " | ".join(informe)
    else:
        informe.append("openai: no hay OPENAI_API_KEY")
    if not clave:
        return None, " | ".join(informe)
    for modelo in modelos or MODELOS:
        for forma in (_por_generate, _por_interacciones):
            try:
                img, nota = forma(pedido, clave, modelo)
            except requests.RequestException as error:
                img, nota = None, f"{forma.__name__}: {type(error).__name__}"
            informe.append(f"{modelo} · {nota}")
            if img:
                return img, " | ".join(informe)
    return None, " | ".join(informe)
