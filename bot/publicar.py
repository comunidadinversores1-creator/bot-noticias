"""Publica en Telegram y en X. Si algo falla, explica el motivo en criollo."""
from __future__ import annotations

import html as html_lib
import json
import os
import re
import time

import requests
from requests_oauthlib import OAuth1

from .texto import peso_x
from .util import log, ocultar

TELEGRAM = "https://api.telegram.org"
X_SUBIR = "https://api.x.com/2/media/upload"
X_POST = "https://api.x.com/2/tweets"


class ErrorPublicacion(Exception):
    """No se pudo publicar. El mensaje ya está listo para mostrarle al dueño."""

    def __init__(self, mensaje: str, clave: str = "error", reintentar: bool = False, incierto: bool = False) -> None:
        super().__init__(mensaje)
        self.clave = clave              # para no mandar el mismo aviso mil veces
        self.reintentar = reintentar    # True si conviene probar de nuevo más tarde
        self.incierto = incierto        # True si no se sabe si llegó a publicarse (no se reintenta: duplicaría)


_SIN_CONEXION = ("NewConnectionError", "Failed to establish", "NameResolutionError", "Failed to resolve", "ConnectTimeoutError",
                 "Name or service not known", "Temporary failure in name resolution", "Connection refused", "getaddrinfo",
                 "nodename nor servname", "Network is unreachable", "No route to host")


def _no_salio(error: Exception) -> bool:
    """True si el pedido seguro NO llegó al otro lado (ni siquiera hubo conexión): se puede reintentar
    sin riesgo de duplicar. Si la conexión se cortó después de enviar, no se sabe."""
    if isinstance(error, (requests.ConnectTimeout, requests.exceptions.SSLError, requests.exceptions.ProxyError)):
        return True
    return isinstance(error, requests.ConnectionError) and any(s in str(error) for s in _SIN_CONEXION)


# ------------------------------------------------------------------ Telegram

def _telegram(token: str, metodo: str, datos: dict, archivos: dict | None = None) -> dict:
    url = f"{TELEGRAM}/bot{token}/{metodo}"
    for intento in range(2):
        try:
            r = requests.post(url, data=datos, files=archivos, timeout=(10, 40))
            respuesta = r.json()
        except requests.RequestException as error:
            if _no_salio(error):
                # Ni siquiera llegó a conectarse: seguro que no se publicó, se puede probar más tarde.
                raise ErrorPublicacion(f"No pude conectarme con Telegram ({type(error).__name__}).", "tg_red", True)
            # Se cortó a mitad de camino: puede haberse publicado igual. No se reintenta para no duplicar.
            raise ErrorPublicacion(f"Se cortó la conexión con Telegram ({type(error).__name__}).", "tg_red", False, incierto=True)
        except ValueError:
            raise ErrorPublicacion(f"Telegram respondió algo raro (código {r.status_code}).", "tg_raro", True)
        if respuesta.get("ok"):
            return respuesta["result"]
        codigo = respuesta.get("error_code")
        detalle = str(respuesta.get("description", "")).lower()
        if codigo == 429 and intento == 0:
            espera = int((respuesta.get("parameters") or {}).get("retry_after", 5))
            if espera <= 30:
                time.sleep(espera + 1)
                continue
        if codigo == 401:
            raise ErrorPublicacion("Telegram rechazó la llave del bot (token). Hay que revisarla o pedir una nueva en BotFather.", "tg_token")
        if "chat not found" in detalle:
            raise ErrorPublicacion("Telegram no encuentra el canal o grupo. Revisar que el destino esté bien escrito y que el bot esté agregado.", "tg_destino")
        if codigo == 403 or "not enough rights" in detalle or "need administrator rights" in detalle:
            raise ErrorPublicacion("El bot no tiene permiso para publicar ahí. Hay que hacerlo administrador del canal o grupo.", "tg_permiso")
        raise ErrorPublicacion(f"Telegram no aceptó la publicación: {respuesta.get('description', codigo)}", "tg_otro", codigo in (429, 500, 502))
    raise ErrorPublicacion("Telegram no respondió.", "tg_red", True)


def separar_destino(destino: str) -> dict:
    """'@canal' o '-1001234567890' -> solo el chat.  '-1001234567890/45' -> el chat y el tema 45 de un grupo
    con temas (el número del tema es el que aparece en su link: t.me/c/1234567890/45)."""
    destino = str(destino or "").strip()
    chat, _, tema = destino.rpartition("/") if re.fullmatch(r".+/\d+", destino) else (destino, "", "")
    datos = {"chat_id": chat}
    if tema:
        datos["message_thread_id"] = tema
    return datos


def telegram_foto(token: str, destino: str, jpg: bytes, texto_html: str) -> int:
    resultado = _telegram(
        token, "sendPhoto",
        {**separar_destino(destino), "caption": texto_html, "parse_mode": "HTML"},
        {"photo": ("noticia.jpg", jpg, "image/jpeg")},
    )
    return int(resultado.get("message_id", 0))


_SIN_VISTA_PREVIA = json.dumps({"is_disabled": True})     # que el link no arme un cartel que tape la noticia


def telegram_texto(token: str, destino: str, texto_html: str) -> int:
    """Publica solo texto (con negritas y links, sin imagen). Devuelve el número del mensaje."""
    resultado = _telegram(
        token, "sendMessage",
        {**separar_destino(destino), "text": texto_html[:4096], "parse_mode": "HTML", "link_preview_options": _SIN_VISTA_PREVIA},
    )
    return int(resultado.get("message_id", 0))


LIMITE_PIE_DE_FOTO = 1024    # Telegram no deja poner más que esto debajo de una foto


def largo_visible(texto_html: str) -> int:
    """Cuántos caracteres ve la gente (sin las marcas de negrita y de link)."""
    return len(html_lib.unescape(re.sub(r"<[^>]+>", "", texto_html or "")))


def telegram_publicar(token: str, destino: str, jpg: bytes | None, texto_html: str) -> int:
    """Publica con foto o sin foto. Si el texto es más largo de lo que entra debajo de una foto
    (pasa con los textos propios de Eze), sale la foto sola y enseguida el texto completo."""
    if jpg is None:
        return telegram_texto(token, destino, texto_html)
    if largo_visible(texto_html) <= LIMITE_PIE_DE_FOTO:
        return telegram_foto(token, destino, jpg, texto_html)
    telegram_foto(token, destino, jpg, "")
    try:
        return telegram_texto(token, destino, texto_html)
    except ErrorPublicacion as error:
        # La foto ya salió: repetir todo la duplicaría.
        error.incierto, error.reintentar = True, False
        raise


def telegram_puede_publicar(token: str, destino: str) -> str:
    """Comprueba, sin publicar nada, que el bot esté en ese canal o grupo y pueda escribir.
    Devuelve una línea para mostrarle al dueño."""
    datos = separar_destino(destino)
    try:
        yo = _telegram(token, "getMe", {})
        chat = _telegram(token, "getChat", {"chat_id": datos["chat_id"]})
        miembro = _telegram(token, "getChatMember", {"chat_id": datos["chat_id"], "user_id": yo["id"]})
    except ErrorPublicacion as error:
        return f"❌ {destino}: {error}"
    nombre = chat.get("title") or destino
    estado = miembro.get("status")
    if chat.get("type") == "channel":
        ok = estado == "creator" or (estado == "administrator" and miembro.get("can_post_messages"))
        falta = "hacelo administrador con permiso para Publicar mensajes"
    else:
        ok = estado in ("creator", "administrator") or (estado == "member" and miembro.get("can_send_messages", True))
        falta = "agregalo al grupo como administrador"
    tema = f" (tema {datos['message_thread_id']})" if datos.get("message_thread_id") else ""
    if ok:
        return f"✅ {nombre}{tema}: puedo publicar"
    return f"❌ {nombre}{tema}: no puedo publicar ({estado}). Solución: {falta}."


def telegram_mensaje(token: str, destino: str, texto: str, html: bool = False) -> None:
    """Un mensaje para el dueño (avisos, muestras). Con html=True acepta negritas y bloques para copiar."""
    datos = {"chat_id": destino, "text": texto[:4000], "link_preview_options": _SIN_VISTA_PREVIA}
    if html:
        datos["parse_mode"] = "HTML"
    _telegram(token, "sendMessage", datos)


def telegram_album(token: str, destino: str, jpgs: list[bytes], texto_html: str = "") -> None:
    """Varias fotos juntas (un carrusel). Telegram acepta de 2 a 10 por álbum; el texto va en la primera."""
    for inicio in range(0, len(jpgs), 10):
        grupo = jpgs[inicio:inicio + 10]
        if len(grupo) == 1:
            telegram_foto(token, destino, grupo[0], texto_html if inicio == 0 else "")
            continue
        medios = []
        for i, _ in enumerate(grupo):
            m = {"type": "photo", "media": f"attach://foto{i}"}
            if i == 0 and inicio == 0 and texto_html:
                m.update(caption=texto_html[:1000], parse_mode="HTML")
            medios.append(m)
        _telegram(token, "sendMediaGroup", dict(separar_destino(destino), media=json.dumps(medios)),
                  {f"foto{i}": (f"foto{i}.jpg", b, "image/jpeg") for i, b in enumerate(grupo)})


def telegram_novedades(token: str, desde: int) -> list[dict]:
    """Los mensajes nuevos que le llegaron al robot (para leer lo que Eze le manda por privado)."""
    resultado = _telegram(token, "getUpdates", {"offset": desde, "limit": 100, "timeout": 0,
                                                "allowed_updates": json.dumps(["message"])})
    return resultado if isinstance(resultado, list) else []


def telegram_bajar(token: str, file_id: str) -> bytes | None:
    """Baja una foto que le mandaron al robot."""
    try:
        info = _telegram(token, "getFile", {"file_id": file_id})
        r = requests.get(f"{TELEGRAM}/file/bot{token}/{info['file_path']}", timeout=60)
        return r.content if r.status_code == 200 else None
    except (ErrorPublicacion, KeyError, TypeError, requests.RequestException):
        return None


def telegram_chats(token: str) -> list[dict]:
    """Chats donde el bot fue agregado o le escribieron hace poco (para averiguar el destino)."""
    try:
        r = requests.post(f"{TELEGRAM}/bot{token}/getUpdates", data={"limit": 100, "timeout": 0}, timeout=30)
        respuesta = r.json()
    except (requests.RequestException, ValueError) as error:
        raise ErrorPublicacion(f"No pude consultar a Telegram ({ocultar(type(error).__name__, token)}).", "tg_red")
    if not respuesta.get("ok"):
        raise ErrorPublicacion(f"Telegram: {respuesta.get('description')}", "tg_otro")
    vistos: dict[int, dict] = {}
    for novedad in respuesta.get("result", []):
        for campo in ("message", "channel_post", "my_chat_member", "edited_message"):
            mensaje = novedad.get(campo) or {}
            chat = mensaje.get("chat")
            if not chat:
                continue
            visto = vistos.setdefault(chat["id"], {
                "id": chat["id"],
                "tipo": chat.get("type"),
                "nombre": chat.get("title") or " ".join(filter(None, [chat.get("first_name"), chat.get("last_name")])),
                "usuario": chat.get("username", ""),
                "temas": {},
            })
            # Grupos con temas: el número del tema y, si se ve, su nombre.
            if mensaje.get("is_topic_message") and mensaje.get("message_thread_id"):
                creado = mensaje.get("forum_topic_created") or (mensaje.get("reply_to_message") or {}).get("forum_topic_created") or {}
                tema = int(mensaje["message_thread_id"])
                visto["temas"][tema] = creado.get("name") or visto["temas"].get(tema) or ""
    return list(vistos.values())


# ------------------------------------------------------------------------- X

def claves_x(entorno: dict | None = None) -> dict | None:
    entorno = os.environ if entorno is None else entorno
    claves = {k: (entorno.get(k) or "").strip() for k in ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_SECRET")}
    return claves if all(claves.values()) else None


def x_quien_soy(claves: dict) -> str:
    """Comprueba las llaves de X sin publicar nada: devuelve el @usuario de la cuenta."""
    firma = OAuth1(claves["X_API_KEY"], claves["X_API_SECRET"], claves["X_ACCESS_TOKEN"], claves["X_ACCESS_SECRET"])
    try:
        r = requests.get("https://api.x.com/2/users/me", auth=firma, timeout=30)
    except requests.RequestException as error:
        raise ErrorPublicacion(f"No pude conectarme con X ({type(error).__name__}).", "x_red", True)
    if r.status_code != 200:
        raise _error_x(r, "la verificación", claves)
    try:
        return "@" + str(r.json()["data"]["username"])
    except (ValueError, KeyError, TypeError):
        return "(sin nombre)"


def _error_x(r: requests.Response, paso: str, claves: dict) -> ErrorPublicacion:
    cuerpo = ocultar(r.text[:400], *claves.values())
    bajo = cuerpo.lower()
    if r.status_code == 401:
        return ErrorPublicacion("X rechazó las llaves. Hay que revisar las 4 claves de X (o generarlas de nuevo).", "x_claves")
    if "suspended" in bajo:
        return ErrorPublicacion("X dice que la cuenta está suspendida. Hay que entrar a X y revisar qué pasó.", "x_suspendida")
    if r.status_code == 402 or re.search(r"\b(credits?|billing|payment|spend(?:ing)?\s+(?:cap|limit)|usage\s+cap)\b", bajo):
        return ErrorPublicacion("X no publicó porque no hay crédito cargado (o se llegó al límite de gasto del mes).", "x_credito")
    if r.status_code == 403 and ("permission" in bajo or "oauth1" in bajo or "not permitted" in bajo):
        return ErrorPublicacion("La app de X no tiene permiso de escritura. Hay que ponerla en 'Read and write' y generar de nuevo el Access Token.", "x_permiso")
    if r.status_code == 403 and "duplicate" in bajo:
        return ErrorPublicacion("X rechazó el texto por ser igual a uno ya publicado.", "x_repetido")
    if r.status_code == 429:
        return ErrorPublicacion("X pidió esperar (demasiadas publicaciones seguidas).", "x_limite", True)
    if r.status_code >= 500:
        return ErrorPublicacion(f"X está con problemas (código {r.status_code}).", "x_caido", True)
    return ErrorPublicacion(f"X no aceptó {paso} (código {r.status_code}): {cuerpo[:200]}", "x_otro")


def x_publicar(claves: dict, jpg: bytes | None, texto: str, publicidad: bool = False, hecha_con_ia: bool = False,
               texto_corto: str = "", en_respuesta_a: str = "", encuesta: list[str] | None = None) -> str:
    """Crea la publicación (con la imagen, si hay una). Devuelve el id del post.

    jpg=None          -> publica solo el texto.
    publicidad=True   -> X le pone la etiqueta de contenido promocional (lo exige su regla
                         cuando hay un link de afiliado o una promo).
    hecha_con_ia=True -> avisa que la imagen fue generada con IA.
    texto_corto       -> si el texto es largo (cuentas con X Premium) y X no lo acepta, se publica este."""
    firma = OAuth1(claves["X_API_KEY"], claves["X_API_SECRET"], claves["X_ACCESS_TOKEN"], claves["X_ACCESS_SECRET"])
    imagen: dict = {}
    if jpg is not None:
        try:
            r = requests.post(
                X_SUBIR, auth=firma, timeout=60,
                files={"media": ("noticia.jpg", jpg, "image/jpeg")},
                data={"media_category": "tweet_image"},
            )
        except requests.RequestException as error:
            raise ErrorPublicacion(f"No pude conectarme con X ({type(error).__name__}).", "x_red", True)
        if r.status_code not in (200, 201):
            raise _error_x(r, "la imagen", claves)
        try:
            imagen = {"media": {"media_ids": [str(r.json()["data"]["id"])]}}
        except (ValueError, KeyError, TypeError):
            raise ErrorPublicacion("X recibió la imagen pero no devolvió su número.", "x_otro", True)

    if texto_corto and peso_x(texto) > 280:
        try:
            return _x_crear(firma, texto, imagen, publicidad, hecha_con_ia, claves)
        except ErrorPublicacion as error:
            if error.incierto or error.clave == "x_red":
                raise
            log.warning("X no aceptó el texto largo (%s); publico la versión corta.", str(error)[:120])
        texto = texto_corto
    if en_respuesta_a:
        imagen = {**imagen, "reply": {"in_reply_to_tweet_id": str(en_respuesta_a)}}
    if encuesta:
        # Encuesta de X: de 2 a 4 opciones de hasta 25 letras, abierta 24 horas (no lleva imagen).
        imagen = {**imagen, "poll": {"options": [o[:25] for o in encuesta[:4]], "duration_minutes": 1440}}
    return _x_crear(firma, texto, imagen, publicidad, hecha_con_ia, claves)


def _x_crear(firma, texto: str, imagen: dict, publicidad: bool, hecha_con_ia: bool, claves: dict) -> str:
    cuerpo: dict = {"text": texto, **imagen}
    if publicidad:
        cuerpo["paid_partnership"] = True
    if hecha_con_ia and imagen:
        cuerpo["made_with_ai"] = True
    try:
        r = requests.post(X_POST, auth=firma, timeout=40, json=cuerpo)
        if r.status_code == 400 and len(cuerpo) > 1 + len(imagen):
            # Si X no acepta las etiquetas en esta cuenta, se publica igual y el aviso va en el texto.
            log.warning("X no aceptó las etiquetas de publicidad/IA; publico con el aviso en el texto.")
            aviso = " #publi" if publicidad and peso_x(texto) <= 272 else ""
            r = requests.post(X_POST, auth=firma, timeout=40, json={"text": texto + aviso, **imagen})
    except requests.RequestException as error:
        if _no_salio(error):
            raise ErrorPublicacion(f"No pude conectarme con X ({type(error).__name__}).", "x_red", True)
        # No se sabe si llegó a publicarse: NO se reintenta para no duplicar.
        raise ErrorPublicacion(f"Se cortó la conexión con X al publicar ({type(error).__name__}).", "x_red", False, incierto=True)
    if r.status_code not in (200, 201):
        raise _error_x(r, "la publicación", claves)
    try:
        return str(r.json()["data"]["id"])
    except (ValueError, KeyError, TypeError):
        log.warning("X publicó pero no devolvió el id.")
        return ""
