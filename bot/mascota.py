"""El personaje de los carruseles (el robot verde de Eze), dibujado por la IA de imágenes de Google en distintas poses.

Estilo fijo: dibujo animado plano, contorno negro grueso, fondo crema liso, sin texto."""
from __future__ import annotations

import base64
import logging

import requests

from .imagen import INTERACCIONES, _primera_imagen

log = logging.getLogger("bot")

PERSONAJE = (
    "A cute friendly cartoon robot mascot with EXACTLY two arms and two hands (never three): a square green head (#22c55e) "
    "with a black screen as its face, on the screen two big friendly white eyes and a small green rising line chart below them, "
    "round black ear speakers on both sides, a short antenna with a small green ball on top, a compact dark green body with a "
    "black panel and three small lights (green, yellow, red), black flexible tube arms with green hands, short black legs and green shoes."
)
ESTILO = (
    "Clean flat 2D cartoon sticker illustration, consistent character design, thick black outlines, simple shapes, bold flat colors (green, black, cream, "
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


def por_cloudflare(pedido: str, cuenta: str, token: str, modelo: str = "@cf/black-forest-labs/flux-1-schnell") -> tuple[bytes | None, str]:
    """Cloudflare Workers AI (cupo gratis diario, sin tarjeta): Flux dibuja la imagen y la devuelve en base64."""
    r = requests.post(f"https://api.cloudflare.com/client/v4/accounts/{cuenta}/ai/run/{modelo}",
                      headers={"Authorization": f"Bearer {token}"}, json={"prompt": pedido[:2000], "steps": 8}, timeout=120)
    if r.status_code != 200:
        return None, f"cloudflare {r.status_code}: {r.text[:200]}"
    try:
        dato = (r.json().get("result") or {}).get("image")
    except ValueError:
        dato = None
    return (base64.b64decode(dato), "ok") if dato else (None, "cloudflare: sin imagen")


def escena(texto: str) -> str:
    """Pedido para una ilustración del tema (sin el robot), en el mismo estilo. Fondo blanco puro: en la lámina
    se funde con el crema (multiply), así no queda un recuadro de otro color."""
    return (f"Scene: {texto}. Clean flat 2D cartoon sticker illustration, thick black outlines, simple shapes, bold flat colors "
            "(green, black, small touches of red, yellow and orange). If the idea is negative (losses, falls, errors, hacks), "
            "arrows and charts point DOWN and are red. Plain pure white background, nothing else around. "
            "No text, no letters, no numbers, no logos, no watermark, no frame.")


def ilustrar(texto: str) -> bytes | None:
    """La ilustración del tema con Cloudflare (gratis). None si no hay llaves o falla: va el robot en SVG."""
    import os
    cuenta, token = os.environ.get("CF_ACCOUNT_ID", ""), os.environ.get("CF_API_TOKEN", "")
    if not (cuenta and token and texto):
        return None
    try:
        img, nota = por_cloudflare(escena(texto), cuenta, token)
    except requests.RequestException as error:
        img, nota = None, type(error).__name__
    if img is None:
        log.warning("ilustración con IA: %s", nota[:200])
    return img


def con_robot(pose: str) -> str:
    return f"{PERSONAJE} Pose and scene: {pose}. {ESTILO}"


def dibujar(pose: str, clave: str, modelos: list[str] | None = None, clave_openai: str = "") -> tuple[bytes | None, str]:
    """(png, informe). Primero la IA de imágenes de ChatGPT (si hay clave), después las de Google."""
    pedido = f"{PERSONAJE} Pose: {pose}. {ESTILO}"
    informe = []
    import os
    if os.environ.get("CF_API_TOKEN") and os.environ.get("CF_ACCOUNT_ID"):
        try:
            img, nota = por_cloudflare(pedido, os.environ["CF_ACCOUNT_ID"], os.environ["CF_API_TOKEN"])
        except requests.RequestException as error:
            img, nota = None, f"cloudflare: {type(error).__name__}"
        informe.append(nota)
        if img:
            return img, " | ".join(informe)
    else:
        informe.append("cloudflare: faltan CF_API_TOKEN / CF_ACCOUNT_ID")
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
