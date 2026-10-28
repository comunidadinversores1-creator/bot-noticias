"""Publicar en Instagram (API de Instagram con inicio de sesión de Instagram).

- Las llaves: IG_TOKEN_EZEBAGNATO e IG_TOKEN_COMUNIDAD (GitHub > Secrets). Duran 60 días; el bot las
  renueva solo cada ~20 días y guarda la nueva CIFRADA en la memoria (el repo es público).
- Instagram pide que cada imagen esté en un link público: el bot la sube un rato a la rama "ig-imagenes"
  del repo, publica, y la borra.
- Después lee los me gusta, comentarios, guardados y alcance de cada carrusel para ver qué estilo rinde más."""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import time

import requests

from .util import log

API = "https://graph.instagram.com/v23.0"
GITHUB = "https://api.github.com"
RAMA = "ig-imagenes"
CUENTAS = {"ezebagnato": "IG_TOKEN_EZEBAGNATO", "comunidad": "IG_TOKEN_COMUNIDAD"}


class ErrorInstagram(Exception):
    pass


# ------------------------------------------------------------- llaves (con renovación cifrada)

def _clave_cifrado() -> bytes:
    base = (os.environ.get("TELEGRAM_BOT_TOKEN") or "") + "|ig|" + (os.environ.get("IG_TOKEN_EZEBAGNATO") or "")[:12]
    return hashlib.sha256(base.encode()).digest()


def cifrar(texto: str) -> str:
    sal = secrets.token_bytes(16)
    clave = _clave_cifrado()
    datos = texto.encode()
    flujo = b"".join(hmac.new(clave, sal + i.to_bytes(4, "big"), hashlib.sha256).digest() for i in range(len(datos) // 32 + 1))
    cuerpo = bytes(a ^ b for a, b in zip(datos, flujo))
    firma = hmac.new(clave, sal + cuerpo, hashlib.sha256).digest()[:16]
    return base64.urlsafe_b64encode(sal + firma + cuerpo).decode()


def descifrar(texto: str) -> str | None:
    try:
        crudo = base64.urlsafe_b64decode(texto.encode())
    except (ValueError, TypeError):
        return None
    sal, firma, cuerpo = crudo[:16], crudo[16:32], crudo[32:]
    clave = _clave_cifrado()
    if not hmac.compare_digest(firma, hmac.new(clave, sal + cuerpo, hashlib.sha256).digest()[:16]):
        return None
    flujo = b"".join(hmac.new(clave, sal + i.to_bytes(4, "big"), hashlib.sha256).digest() for i in range(len(cuerpo) // 32 + 1))
    return bytes(a ^ b for a, b in zip(cuerpo, flujo)).decode(errors="ignore")


def llave(cuenta: str, memoria: dict) -> str:
    """La llave más nueva: la renovada (cifrada en la memoria) o, si no hay, la de GitHub."""
    guardada = (memoria.get("llaves") or {}).get(cuenta) or {}
    if guardada.get("cifrada"):
        t = descifrar(guardada["cifrada"])
        if t:
            return t
    return (os.environ.get(CUENTAS[cuenta]) or "").strip()


def disponibles(memoria: dict) -> list[str]:
    return [c for c in CUENTAS if llave(c, memoria)]


def renovar(cuenta: str, memoria: dict, ahora: float | None = None) -> bool:
    """Cada ~20 días pide una llave nueva (duran 60). Si falla, sigue con la que hay."""
    ahora = ahora or time.time()
    g = memoria.setdefault("llaves", {}).setdefault(cuenta, {})
    if ahora - float(g.get("renovada", 0) or 0) < 20 * 86400:
        return False
    t = llave(cuenta, memoria)
    if not t:
        return False
    try:
        r = requests.get(f"{API.rsplit('/', 1)[0]}/refresh_access_token",
                         params={"grant_type": "ig_refresh_token", "access_token": t}, timeout=30)
        nueva = (r.json() or {}).get("access_token") if r.status_code == 200 else None
    except (requests.RequestException, ValueError):
        nueva = None
    g["renovada"] = ahora                  # aunque falle, no se reintenta en cada corrida
    if nueva:
        g["cifrada"] = cifrar(nueva)
        return True
    log.warning("Instagram (%s): no pude renovar la llave (sigue la actual).", cuenta)
    return False


# ------------------------------------------------------------- llamadas

def _pedir(metodo: str, ruta: str, token: str, **datos) -> dict:
    try:
        if metodo == "GET":
            r = requests.get(f"{API}/{ruta}", params={**datos, "access_token": token}, timeout=60)
        else:
            r = requests.post(f"{API}/{ruta}", data={**datos, "access_token": token}, timeout=60)
        cuerpo = r.json()
    except (requests.RequestException, ValueError) as error:
        raise ErrorInstagram(f"Instagram no respondió ({type(error).__name__}).")
    if r.status_code != 200 or "error" in cuerpo:
        e = cuerpo.get("error") or {}
        if e.get("code") == 190:
            raise ErrorInstagram("Instagram rechazó la llave (venció o la borraron). Hay que generar una nueva en Meta y pegarla en GitHub.")
        raise ErrorInstagram(f"Instagram no aceptó el pedido: {e.get('message') or r.status_code}")
    return cuerpo


def quien_soy(token: str) -> dict:
    return _pedir("GET", "me", token, fields="user_id,username,followers_count,media_count")


def _esperar(contenedor: str, token: str, intentos: int = 20) -> None:
    time.sleep(2)                                     # Instagram a veces todavía no "ve" el contenedor recién creado
    for i in range(intentos):
        try:
            estado = _pedir("GET", contenedor, token, fields="status_code").get("status_code")
        except ErrorInstagram as error:
            # "The requested resource does not exist" justo después de crearlo es un tropiezo pasajero de Instagram: se espera y se mira de nuevo.
            if "does not exist" in str(error) and i < 8:
                time.sleep(3)
                continue
            raise
        if estado == "FINISHED":
            return
        if estado in ("ERROR", "EXPIRED"):
            raise ErrorInstagram(f"Instagram no pudo procesar la imagen ({estado}).")
        time.sleep(3)
    raise ErrorInstagram("Instagram tardó demasiado en procesar las imágenes.")


def publicar_carrusel(token: str, urls: list[str], texto: str) -> str:
    """Devuelve el id de la publicación."""
    yo = quien_soy(token)["user_id"]
    hijos = []
    for url in urls[:20]:
        c = _pedir("POST", f"{yo}/media", token, image_url=url, is_carousel_item="true")["id"]
        hijos.append(c)
    for c in hijos:
        _esperar(c, token)
    padre = _pedir("POST", f"{yo}/media", token, media_type="CAROUSEL", children=",".join(hijos), caption=texto[:2200])["id"]
    _esperar(padre, token)
    return _pedir("POST", f"{yo}/media_publish", token, creation_id=padre)["id"]


def publicar_historia(token: str, url: str) -> str:
    yo = quien_soy(token)["user_id"]
    for intento in range(2):
        try:
            c = _pedir("POST", f"{yo}/media", token, image_url=url, media_type="STORIES")["id"]
            _esperar(c, token)
            return _pedir("POST", f"{yo}/media_publish", token, creation_id=c)["id"]
        except ErrorInstagram as error:
            # Si Instagram dice que "el recurso no existe" antes de publicar, se arma un contenedor nuevo una vez más
            # (nunca hay riesgo de duplicar: la historia no llegó a publicarse).
            if intento == 0 and "does not exist" in str(error):
                time.sleep(5)
                continue
            raise


def comentarios(token: str, media_id: str) -> list[dict]:
    datos = _pedir("GET", f"{media_id}/comments", token, fields="id,text,username,timestamp", limit="50")
    return [c for c in (datos.get("data") or []) if isinstance(c, dict)]


def responder_comentario(token: str, comentario_id: str, texto: str) -> str:
    return _pedir("POST", f"{comentario_id}/replies", token, message=texto[:300])["id"]


def mensaje_privado(token: str, yo: str, comentario_id: str, texto: str) -> None:
    """Respuesta privada (DM) a quien comentó. Instagram la permite una vez por comentario, hasta 7 días después."""
    try:
        r = requests.post(f"{API}/{yo}/messages", params={"access_token": token}, timeout=60,
                          json={"recipient": {"comment_id": comentario_id}, "message": {"text": texto[:1000]}})
        cuerpo = r.json()
    except (requests.RequestException, ValueError) as error:
        raise ErrorInstagram(f"Instagram no respondió ({type(error).__name__}).")
    if r.status_code != 200 or "error" in cuerpo:
        e = cuerpo.get("error") or {}
        raise ErrorInstagram(f"no me dejó mandar el DM: {e.get('message') or r.status_code}")


def numeros(token: str, media_id: str) -> dict:
    """Me gusta, comentarios, guardados, compartidos y alcance de una publicación."""
    datos = _pedir("GET", f"{media_id}/insights", token, metric="reach,likes,comments,saved,shares,total_interactions")
    salida = {}
    for m in datos.get("data") or []:
        valores = m.get("values") or [{}]
        salida[m.get("name")] = int((m.get("total_value") or {}).get("value", valores[0].get("value", 0)) or 0)
    return salida


# ------------------------------------------------------------- imágenes en un link público (GitHub)

def _gh(metodo: str, ruta: str, **json_) -> requests.Response:
    token = os.environ.get("GITHUB_TOKEN") or ""
    cab = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    return requests.request(metodo, f"{GITHUB}/repos/{os.environ.get('GITHUB_REPOSITORY', '')}/{ruta}",
                            headers=cab, json=json_ or None, timeout=60)


def subir_imagenes(jpgs: list[bytes], carpeta: str) -> list[str]:
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not (repo and os.environ.get("GITHUB_TOKEN")):
        raise ErrorInstagram("No tengo dónde subir las imágenes (falta GITHUB_TOKEN en la corrida).")
    if _gh("GET", f"git/ref/heads/{RAMA}").status_code == 404:
        base = _gh("GET", "git/ref/heads/main").json()["object"]["sha"]
        _gh("POST", "git/refs", ref=f"refs/heads/{RAMA}", sha=base)
    urls = []
    for i, b in enumerate(jpgs, 1):
        ruta = f"ig/{carpeta}/{i:02d}.jpg"
        r = _gh("PUT", f"contents/{ruta}", message="Imagen para Instagram", branch=RAMA,
                content=base64.b64encode(b).decode())
        if r.status_code not in (200, 201):
            raise ErrorInstagram(f"No pude subir la imagen {i} ({r.status_code}).")
        urls.append(f"https://raw.githubusercontent.com/{repo}/{RAMA}/{ruta}")
    return urls


def borrar_imagenes(carpeta: str, cantidad: int) -> None:
    for i in range(1, cantidad + 1):
        ruta = f"ig/{carpeta}/{i:02d}.jpg"
        try:
            sha = _gh("GET", f"contents/{ruta}?ref={RAMA}").json().get("sha")
            if sha:
                _gh("DELETE", f"contents/{ruta}", message="Ya publicada", branch=RAMA, sha=sha)
        except (requests.RequestException, ValueError):
            continue
