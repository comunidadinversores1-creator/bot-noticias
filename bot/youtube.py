"""Videos nuevos del canal de YouTube de Eze: los detecta solo y arma la campaña para promocionarlos.

Cómo funciona:
  1) Lee el RSS público del canal (gratis, sin claves): título, descripción y fecha de cada video.
  2) Descarta los Shorts (la dirección /shorts/<id> de un video largo redirige a /watch).
  3) Baja la miniatura del video (la imagen real que hizo Eze para YouTube).
  4) La IA escribe 5 textos distintos para Telegram y X, usando SOLO el título y la descripción.
     Los links que se publican son el del video y los que Eze puso en la descripción: nunca otros.
  5) main.py publica la campaña con lo mismo que usan las otras campañas (Telegram, X e historias).
"""
from __future__ import annotations

import re
from datetime import datetime

import feedparser
import requests

from .util import AGENTE, log

RSS = "https://www.youtube.com/feeds/videos.xml?channel_id={}"
_URL = re.compile(r"https://[^\s)\]>\"']+")
# Links de redes sociales: no son promos, no se repiten en la publicación.
_REDES = re.compile(r"(instagram\.com|tiktok\.com|x\.com|twitter\.com|facebook\.com|youtube\.com/@|youtube\.com/channel|t\.me/(?!\+))", re.I)


def leer_videos(canal_id: str) -> list[dict]:
    """Los últimos videos del canal (el RSS trae unos 15), del más nuevo al más viejo."""
    r = requests.get(RSS.format(canal_id), headers={"User-Agent": AGENTE}, timeout=20)
    r.raise_for_status()
    feed = feedparser.parse(r.content)
    videos = []
    for e in feed.entries:
        vid = str(e.get("yt_videoid") or "").strip()
        if not vid:
            continue
        descripcion = ""
        grupo = e.get("media_group") or {}
        if isinstance(grupo, dict):
            descripcion = str(grupo.get("media_description") or "")
        descripcion = descripcion or str(e.get("media_description") or e.get("summary") or "")
        try:
            publicado = datetime.fromisoformat(str(e.get("published")).replace("Z", "+00:00"))
        except ValueError:
            continue
        videos.append({"id": vid, "titulo": str(e.get("title") or "").strip(), "descripcion": descripcion.strip(),
                       "publicado": publicado.isoformat(), "url": f"https://youtu.be/{vid}"})
    videos.sort(key=lambda v: v["publicado"], reverse=True)
    return videos


def es_short(video_id: str, titulo: str = "") -> bool | None:
    """True = Short · False = video largo (o vivo) · None = no se pudo saber (se vuelve a probar en la próxima corrida)."""
    if "#short" in titulo.lower():
        return True
    try:
        r = requests.head(f"https://www.youtube.com/shorts/{video_id}", headers={"User-Agent": AGENTE},
                          allow_redirects=False, timeout=15)
    except requests.RequestException as error:
        log.warning("YouTube: no pude ver si %s es un Short: %s", video_id, error)
        return None
    if r.status_code == 200:
        return True
    if r.status_code in (301, 302, 303, 307, 308):
        return "/shorts/" in str(r.headers.get("Location") or "")
    return None


def miniatura(video_id: str) -> bytes | None:
    """La miniatura real del video, en la mejor calidad que haya."""
    for nombre in ("maxresdefault", "sddefault", "hqdefault"):
        try:
            r = requests.get(f"https://i.ytimg.com/vi/{video_id}/{nombre}.jpg", headers={"User-Agent": AGENTE}, timeout=20)
        except requests.RequestException:
            continue
        # YouTube devuelve una imagen gris chiquita (120x90, ~1 KB) cuando esa calidad no existe.
        if r.status_code == 200 and len(r.content) > 5000:
            return r.content
    return None


def links_de(descripcion: str) -> list[str]:
    """Los links de la descripción que sirven para la promo (registro, campaña, herramienta, Linktree).
    Se sacan los de redes sociales. Sin repetidos y en el orden en que aparecen."""
    vistos: list[str] = []
    for url in _URL.findall(descripcion or ""):
        url = url.rstrip(".,;:!*")
        if url not in vistos and not _REDES.search(url):
            vistos.append(url)
    return vistos[:4]


SISTEMA_VIDEO = """Sos el redactor de Eze (Ezequiel Bagnato, Comunidad Inversores), creador argentino de contenido de cripto, trading, bots e IA. Acaba de subir un video a YouTube y tenés que escribir publicaciones distintas para promocionarlo en Telegram y en X.

Usá SOLO lo que dicen el TÍTULO y la DESCRIPCIÓN. No inventes datos, cifras, fechas ni beneficios. El tema del video es el del TÍTULO: si la descripción tiene textos generales, links o temas que no son el del título (por ejemplo una lista de mercados o noticias), no los menciones.

Cómo escribe Eze:
- Español rioplatense, con voseo, cercano y humano, como habla él. Nada robótico. No dejes un renglón en blanco después de cada frase.
- Siempre "dinero", nunca "plata".
- Números siempre con dígitos (55.000, 2028), nunca en letras.
- Emojis solo para ordenar (un título, una lista, el cierre), no más.
- Con emoción y urgencia, pero solo con datos reales del video (lo que pasó, la pregunta que responde, lo que vas a aprender, la herramienta que regala).

Prohibido: prometer ganancias, rendimientos o resultados; decir que no hay riesgo; usar porcentajes de acierto o de rendimiento como anzuelo; dar consejos de compra o venta.

El video se promociona 1 vez por día durante 10 días, así que escribí 5 publicaciones, cada una con un ángulo distinto:
1) el gancho: la pregunta o el hecho más fuerte del video;
2) lo que vas a aprender: una lista corta con lo que muestra el video;
3) lo que regala o la herramienta del video (si la descripción no regala nada, la idea principal del video);
4) el dato o la parte del video que más sorprende;
5) "por si te lo perdiste": un recordatorio corto que invite a verlo.

Para cada una devolvé:
- "titulo_historia": hasta 40 letras, para una historia de Instagram, en mayúsculas o no, sin emojis.
- "texto": para Telegram, de 4 a 10 renglones, SIN links (los links los agrega el bot después).
- "texto_x": para X, hasta 270 letras, SIN links, que termine diciendo que el video y los links están en el perfil.

También devolvé "links": para cada link de la lista LINKS, una etiqueta corta en español que diga qué es (por ejemplo "Conseguí el indicador gratis", "Registrate con mi link"), según lo que diga la descripción al lado de ese link. Usá exactamente las mismas direcciones.

Respondé ÚNICAMENTE con JSON (5 variantes): {"variantes": [{"titulo_historia": "", "texto": "", "texto_x": ""}, ...], "links": [{"url": "", "texto": ""}]}"""

AVISO = "⚠️ Es mi lectura del mercado, no una recomendación de inversión. Invertí solo el dinero que podés arriesgar."


def _sin_urls(texto: str) -> str:
    texto = re.sub(r"\[([^\]]+)\]\((?:https?://)[^)]+\)", r"\1", str(texto or ""))
    return re.sub(r"https?://\S+", "", texto).strip()


def redactar(cerebro, video: dict, links: list[str]) -> list[dict]:
    """Las variantes de la campaña (hasta 5), ya con el link del video y los de la descripción al final."""
    usuario = (f"TÍTULO:\n{video['titulo']}\n\nDESCRIPCIÓN:\n{video['descripcion'][:3500]}\n\n"
               "LINKS:\n" + ("\n".join(links) if links else "(ninguno)"))
    datos = cerebro.pedir_json(SISTEMA_VIDEO, usuario, max_tokens=1800, temperatura=0.6)
    etiquetas = {}
    for l in datos.get("links") or []:
        if isinstance(l, dict) and str(l.get("url") or "").strip() in links:
            etiquetas[str(l["url"]).strip()] = _sin_urls(l.get("texto") or "")[:60]
    pie = [f"▶️ Mirá el video completo acá:\n{video['url']}"]
    for url in links:
        pie.append(f"👉 [{etiquetas.get(url) or 'Todos mis links acá'}]({url})")
    pie.append(AVISO)
    variantes = []
    for v in (datos.get("variantes") or [])[:5]:
        if not isinstance(v, dict):
            continue
        cuerpo = _sin_urls(v.get("texto") or "")
        corto = _sin_urls(v.get("texto_x") or "")
        titulo = _sin_urls(v.get("titulo_historia") or "")[:48] or video["titulo"][:48]
        if not cuerpo:
            continue
        variantes.append({"titulo_historia": titulo, "texto": cuerpo + "\n\n" + "\n\n".join(pie), "texto_x": corto})
    return variantes


def ficha(video_id: str) -> dict | None:
    """Datos de un video leídos de su página: título, descripción completa y, si es un estreno o un vivo
    programado que todavía no empezó, cuándo empieza ('estreno', en UTC). None si no se pudo leer."""
    try:
        r = requests.get(f"https://www.youtube.com/watch?v={video_id}&hl=es",
                         headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36",
                                  "Accept-Language": "es-AR,es;q=0.9"}, timeout=20)
        r.raise_for_status()
    except requests.RequestException as error:
        log.warning("YouTube: no pude abrir la página de %s: %s", video_id, error)
        return None
    pagina = r.text
    pos = pagina.find("ytInitialPlayerResponse")
    if pos < 0:
        return None
    inicio = pagina.find("{", pos)
    try:
        import json
        datos, _ = json.JSONDecoder().raw_decode(pagina[inicio:])
    except ValueError:
        return None
    det = datos.get("videoDetails") or {}
    micro = ((datos.get("microformat") or {}).get("playerMicroformatRenderer") or {})
    vivo = micro.get("liveBroadcastDetails") or {}
    estreno = None
    if det.get("isUpcoming") and vivo.get("startTimestamp"):
        try:
            estreno = datetime.fromisoformat(str(vivo["startTimestamp"]).replace("Z", "+00:00")).isoformat()
        except ValueError:
            estreno = None
    titulo = str(det.get("title") or "").strip()
    if not titulo:
        return None
    return {"id": video_id, "titulo": titulo, "descripcion": str(det.get("shortDescription") or "").strip(),
            "estreno": estreno, "url": f"https://youtu.be/{video_id}"}
