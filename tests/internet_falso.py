"""Un 'internet de mentira' para probar el bot entero sin conectarse a nada.

Reemplaza el último eslabón de la librería requests (el que de verdad abre la
conexión), así todo lo demás corre igual que en la realidad: se arman las
direcciones, los formularios, la firma de X, etc. Cada servicio responde con el
mismo formato que el servicio real según su documentación."""
from __future__ import annotations

import base64
import io
import json
import re
from datetime import datetime, timedelta
from email.utils import format_datetime
from urllib.parse import parse_qs, urlparse

import requests
from PIL import Image
from requests.structures import CaseInsensitiveDict


def _rss(titulo_feed: str, items: list[dict]) -> bytes:
    partes = [f'<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>{titulo_feed}</title>']
    for it in items:
        fuente = f'<source url="https://x.example">{it["source"]}</source>' if it.get("source") else ""
        partes.append(
            f"<item><title><![CDATA[{it['title']}]]></title><link>https://ejemplo.test/{abs(hash(it['title']))}</link>"
            f"<pubDate>{format_datetime(it['fecha'])}</pubDate>"
            f"<description><![CDATA[{it.get('desc', '')}]]></description>{fuente}</item>"
        )
    partes.append("</channel></rss>")
    return "".join(partes).encode("utf-8")


def _jpg(ancho: int = 1280, alto: int = 853) -> bytes:
    img = Image.new("RGB", (ancho, alto))
    px = img.load()
    for y in range(alto):
        for x in range(ancho):
            px[x, y] = (30 + 180 * x // ancho, 60 + 120 * y // alto, 120)
    b = io.BytesIO()
    img.save(b, "JPEG", quality=80)
    return b.getvalue()


class InternetFalso:
    def __init__(self, ahora: datetime) -> None:
        self.ahora = ahora
        self.pedidos: list[requests.PreparedRequest] = []
        self.telegram_fotos: list[dict] = []
        self.telegram_albumes: list[dict] = []
        self.telegram_mensajes: list[dict] = []
        self.x_posts: list[dict] = []
        self.x_subidas: list[requests.PreparedRequest] = []
        self.llamadas_ia: list[dict] = []
        self.foto_elegida = 0            # lo que contesta la IA al elegir foto
        self.cifra_inventada = 0         # cuántas veces la IA "inventa" una cifra antes de portarse bien
        self.ia_estado = 200             # para simular caídas de la IA
        self.telegram_error: dict | None = None
        self.x_estado = 201
        self.x_cuerpo: dict | None = None
        self.promo_tramposa = 0          # cuántas veces la IA "promete ganancias" en una promo antes de corregirse
        self.promo_sutil = 0             # ídem, pero con una promesa que ninguna lista de palabras detecta
        self.juez_promete = False        # lo que contesta la IA cuando revisa una promo (segunda revisión)
        self.revisiones: list[str] = []  # los textos que le llegaron a esa revisión
        self.para_la_marca = "buena"     # lo que dice la IA sobre cómo le cae la noticia al socio
        self.alterar: dict = {}          # 'elegir' / 'redactar' / 'promo' -> función que deforma la respuesta de la IA
        self.telegram_excepcion: Exception | None = None   # para simular cortes de conexión con Telegram
        self.imagenes_ia: list[dict] = []
        self._jpg = _jpg()
        hace = lambda minutos: ahora - timedelta(minutes=minutos)
        self.feeds = {
            "ambito.com": _rss("Ámbito", [
                {"title": "El BCRA compró USD 150 millones y las reservas superan los USD 40.000 millones", "fecha": hace(30),
                 "desc": "El Banco Central sumó USD 150 millones en la rueda de hoy. Las reservas brutas cerraron en USD 40.120 millones."},
                {"title": "ANSES: cuándo cobro la jubilación de octubre si mi DNI termina en 4", "fecha": hace(20), "desc": "Calendario de pagos de octubre."},
                {"title": "eToro lanza una nueva función de copy trading en Argentina", "fecha": hace(15), "desc": "La plataforma eToro presentó novedades para clientes locales."},
                {"title": "Una noticia vieja que ya no sirve para nada publicar hoy", "fecha": hace(60 * 30), "desc": "Vieja."},
            ]),
            "infobae.com": _rss("Infobae", [
                {"title": "El BCRA compró USD 150 millones y las reservas superan los USD 40.000 millones otra vez", "fecha": hace(25),
                 "desc": "Con la compra de hoy, el Banco Central acumula ocho ruedas seguidas con saldo positivo."},
            ]),
            "federalreserve.gov": _rss("FRB", [
                {"title": "Federal Reserve issues FOMC statement: rate cut of 25 basis points", "fecha": hace(10),
                 "desc": "The Committee decided to lower the target range for the federal funds rate by 25 basis points to 3.50 percent."},
            ]),
            "news.google.com": _rss("Google", [
                {"title": "La Fed recortó la tasa de interés 25 puntos básicos - Reuters", "source": "Reuters", "fecha": hace(8),
                 "desc": "<ol><li><a>La Fed recortó</a> Reuters</li><li><a>Recorte de la Fed</a> Bloomberg</li><li><a>Fed baja</a> EFE</li></ol>"},
            ]),
            "coindesk.com": _rss("CoinDesk", [
                {"title": "Bitcoin ETF investors head for the exit in the biggest rush in months", "fecha": hace(50),
                 "desc": "Spot bitcoin ETFs saw net outflows of $1.2 billion this week, the largest since June."},
            ]),
        }

    def _busqueda_gratis(self, pedido):
        """Gemini con la búsqueda de Google (el respaldo gratis de Grok)."""
        self.busquedas_gratis = getattr(self, "busquedas_gratis", 0) + 1
        consigna = json.loads(self._cuerpo(pedido))["contents"][0]["parts"][0]["text"]
        if "los 6 temas" in consigna:
            contenido = {"temas": [{"tema": "dólar blue sube", "contexto": "El dólar blue subió $20 en el día.", "hashtag": "Dólar", "cuentas": []}]}
        elif "ORIGINALES" in consigna:
            contenido = {"posts": [{"formato": "dilema", "borrador": "imaginá un trader que gana 10 operaciones seguidas\n\n¿sube el lote o lo deja igual?"}]}
        elif "acaba de salir" in consigna:
            contenido = {"cita": "ojo con el dólar esta semana"}
        else:
            contenido = {"posts": [{"busqueda": "dólar blue", "de_que_habla": "El dólar blue subió.", "idea": "yo miro primero el stop loss"}]}
        texto = json.dumps(contenido, ensure_ascii=False)
        return self._r(pedido, 200, {"candidates": [{"content": {"parts": [{"text": texto}]}}]})

    # ------------------------------------------------------------ enrutador
    def enviar(self, pedido: requests.PreparedRequest, **_) -> requests.Response:
        self.pedidos.append(pedido)
        u = urlparse(pedido.url)
        host = u.netloc.lower().removeprefix("www.")
        if host == "api.telegram.org":
            return self._telegram(pedido, u)
        if host == "api.x.com":
            return self._x(pedido, u)
        if host == "generativelanguage.googleapis.com" and u.path.endswith("/interactions"):
            return self._imagen_ia(pedido)
        if host == "generativelanguage.googleapis.com" and ":generateContent" in u.path:
            return self._busqueda_gratis(pedido)
        if host in ("generativelanguage.googleapis.com", "api.groq.com", "api.openai.com", "api.anthropic.com"):
            return self._ia(pedido, host)
        if host.endswith("wikipedia.org") or host == "commons.wikimedia.org":
            return self._wiki(pedido, u, host)
        if host == "api.x.ai" and getattr(self, "grok_sin_saldo", False):
            return self._r(pedido, 402, {"error": "no credits"})
        if host == "api.x.ai":
            cuerpo = json.loads(self._cuerpo(pedido))
            consigna = cuerpo["input"][0]["content"]
            datos = getattr(self, "grok", {})
            if "temas con más conversación" in consigna:
                contenido = {"temas": datos.get("temas", []), "ganchos": datos.get("ganchos", ["te pagan 1.500 usd por mes pero..."])}
            elif "borrador ORIGINAL" in consigna:
                contenido = {"posts": [{"url": "https://x.com/chica/status/5", "cuenta": "@chica", "por_que_funciono": "un dilema con números",
                                        "borrador": "imaginá un trader que gana 10 operaciones seguidas\n\n¿sube el lote o lo deja igual?"},
                                       {"url": "https://x.com/otra/status/6", "cuenta": "@otra", "por_que_funciono": "promesa",
                                        "borrador": "con este bot tenés ganancias aseguradas"}]}
            elif "acaba de salir" in consigna:
                contenido = getattr(self, "cita", {})
            else:
                contenido = {"posts": datos.get("posts", [])}
            return self._r(pedido, 200, {"output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps(contenido, ensure_ascii=False)}]}]})
        if host == "nfs.faireconomy.media":
            return self._r(pedido, 200, list(getattr(self, "calendario", [])))
        if host == "upload.wikimedia.org":
            return self._r(pedido, 200, self._jpg, {"content-type": "image/jpeg"})
        for dominio, contenido in self.feeds.items():
            if host.endswith(dominio):
                return self._r(pedido, 200, contenido, {"content-type": "application/rss+xml"})
        return self._r(pedido, 404, b"no existe")

    @staticmethod
    def _r(pedido, estado: int, cuerpo, cabeceras: dict | None = None) -> requests.Response:
        r = requests.Response()
        r.status_code = estado
        if isinstance(cuerpo, (dict, list)):
            cuerpo = json.dumps(cuerpo).encode("utf-8")
            cabeceras = {"content-type": "application/json", **(cabeceras or {})}
        r._content = cuerpo if isinstance(cuerpo, bytes) else str(cuerpo).encode("utf-8")
        r.headers = CaseInsensitiveDict(cabeceras or {})
        r.url = pedido.url
        r.request = pedido
        r.encoding = "utf-8"
        return r

    @staticmethod
    def _cab(pedido, nombre: str) -> str:
        valor = pedido.headers.get(nombre, "")
        return valor.decode("latin-1") if isinstance(valor, bytes) else str(valor)

    @staticmethod
    def _cuerpo(pedido) -> bytes:
        cuerpo = pedido.body or b""
        if hasattr(cuerpo, "read"):
            cuerpo = cuerpo.read()
        return cuerpo if isinstance(cuerpo, bytes) else str(cuerpo).encode("utf-8")

    # ------------------------------------------------------------- Telegram
    def _telegram(self, pedido, u) -> requests.Response:
        metodo = u.path.rsplit("/", 1)[-1]
        cuerpo = self._cuerpo(pedido)
        if self.telegram_excepcion is not None and metodo == "sendPhoto":
            raise self.telegram_excepcion
        if self.telegram_error:
            return self._r(pedido, self.telegram_error.get("error_code", 400), {"ok": False, **self.telegram_error})
        if metodo == "sendPhoto":
            tipo = self._cab(pedido, "Content-Type")
            assert tipo.startswith("multipart/form-data"), tipo
            campos = dict(re.findall(rb'name="(\w+)"\r\n\r\n(.*?)\r\n--', cuerpo, re.S))
            assert b'name="photo"; filename="noticia.jpg"' in cuerpo
            self.telegram_fotos.append({k.decode(): v.decode("utf-8") for k, v in campos.items()})
            return self._r(pedido, 200, {"ok": True, "result": {"message_id": 100 + len(self.telegram_fotos)}})
        if metodo == "sendMediaGroup":
            medios = json.loads(dict(re.findall(rb'name="(\w+)"\r\n\r\n(.*?)\r\n--', cuerpo, re.S))[b"media"].decode("utf-8"))
            self.telegram_albumes.append({"fotos": len(medios), "texto": medios[0].get("caption", "")})
            return self._r(pedido, 200, {"ok": True, "result": [{"message_id": 300}]})
        if metodo == "sendMessage":
            campos = {k: v[0] for k, v in parse_qs(cuerpo.decode("utf-8")).items()}
            self.telegram_mensajes.append(campos)
            return self._r(pedido, 200, {"ok": True, "result": {"message_id": 1}})
        if metodo == "getUpdates" and "offset" in parse_qs(cuerpo.decode("utf-8")):
            datos = {k: v[0] for k, v in parse_qs(cuerpo.decode("utf-8")).items()}
            desde = int(datos.get("offset", 0))
            return self._r(pedido, 200, {"ok": True, "result": [u for u in getattr(self, "actualizaciones", []) if u["update_id"] >= desde]})
        if metodo == "getUpdates":
            return self._r(pedido, 200, {"ok": True, "result": [
                {"update_id": 1, "message": {"chat": {"id": 555, "type": "private", "first_name": "Eze"}}},
                {"update_id": 2, "channel_post": {"chat": {"id": -100123, "type": "channel", "title": "Canal de prueba", "username": "canalprueba"}}},
            ]})
        return self._r(pedido, 404, {"ok": False, "error_code": 404, "description": "Not Found"})

    # -------------------------------------------------------------------- X
    def _x(self, pedido, u) -> requests.Response:
        autorizacion = self._cab(pedido, "Authorization")
        assert autorizacion.startswith("OAuth "), "X exige la firma OAuth 1.0a"
        for pieza in ("oauth_consumer_key", "oauth_token", "oauth_signature", "oauth_nonce", "oauth_timestamp"):
            assert pieza in autorizacion, pieza
        if u.path == "/2/media/upload":
            cuerpo = self._cuerpo(pedido)
            assert self._cab(pedido, "Content-Type").startswith("multipart/form-data")
            assert b'name="media_category"\r\n\r\ntweet_image' in cuerpo
            assert b'name="media"; filename="noticia.jpg"' in cuerpo
            self.x_subidas.append(pedido)
            return self._r(pedido, 200, {"data": {"id": "1880000000000000001", "media_key": "3_188", "size": len(cuerpo)}})
        if u.path == "/2/tweets":
            assert self._cab(pedido, "Content-Type").startswith("application/json")
            datos = json.loads(self._cuerpo(pedido))
            if self.x_estado not in (200, 201):
                return self._r(pedido, self.x_estado, self.x_cuerpo or {"title": "error", "detail": "algo falló"})
            if getattr(self, "x_rechaza_largos", False) and len(datos["text"]) > 280:
                return self._r(pedido, 403, {"title": "Forbidden", "detail": "Your Tweet text is too long."})
            self.x_posts.append(datos)
            return self._r(pedido, 201, {"data": {"id": f"19{len(self.x_posts):018d}", "text": datos["text"]}})
        return self._r(pedido, 404, {"title": "Not Found"})

    # ------------------------------------------------------------------- IA
    def _ia(self, pedido, host: str) -> requests.Response:
        datos = json.loads(self._cuerpo(pedido))
        self.llamadas_ia.append({"host": host, **datos})
        if self.ia_estado != 200:
            return self._r(pedido, self.ia_estado, {"error": {"message": "caída simulada"}})
        if host == "api.anthropic.com":
            sistema, usuario = datos["system"], datos["messages"][0]["content"]
        else:
            sistema, usuario = datos["messages"][0]["content"], datos["messages"][1]["content"]

        if "carruseles de Instagram" in sistema:
            contenido = json.dumps(getattr(self, "carrusel", {}), ensure_ascii=False)
            if host == "api.anthropic.com":
                return self._r(pedido, 200, {"content": [{"type": "text", "text": contenido}]})
            return self._r(pedido, 200, {"choices": [{"message": {"role": "assistant", "content": contenido}}]})
        if "Eze te manda notas" in sistema:
            contenido = json.dumps(getattr(self, "material", {}), ensure_ascii=False)
            if host == "api.anthropic.com":
                return self._r(pedido, 200, {"content": [{"type": "text", "text": contenido}]})
            return self._r(pedido, 200, {"choices": [{"message": {"role": "assistant", "content": contenido}}]})
        if "hilo semanal de X" in sistema:
            contenido = json.dumps(getattr(self, "hilo", {}), ensure_ascii=False)
            if host == "api.anthropic.com":
                return self._r(pedido, 200, {"content": [{"type": "text", "text": contenido}]})
            return self._r(pedido, 200, {"choices": [{"message": {"role": "assistant", "content": contenido}}]})
        if "cuenta de X de Ezequiel" in sistema and "FORMATO: encuesta" in usuario and getattr(self, "encuesta", None):
            contenido = json.dumps(self.encuesta, ensure_ascii=False)
            if host == "api.anthropic.com":
                return self._r(pedido, 200, {"content": [{"type": "text", "text": contenido}]})
            return self._r(pedido, 200, {"choices": [{"message": {"role": "assistant", "content": contenido}}]})
        if "cuenta de X de Ezequiel" in sistema:
            textos = getattr(self, "charlas", None) or [
                "te pagan 3.000 usd por mes pero tu dinero lo maneja un bot que no podés apagar\n\n¿aceptás?"]
            indice = getattr(self, "charla_n", 0)
            self.charla_n = indice + 1
            contenido = json.dumps({"texto": textos[min(indice, len(textos) - 1)]}, ensure_ascii=False)
            if host == "api.anthropic.com":
                return self._r(pedido, 200, {"content": [{"type": "text", "text": contenido}]})
            return self._r(pedido, 200, {"choices": [{"message": {"role": "assistant", "content": contenido}}]})
        if "editor jefe" in sistema:
            elegidas = []
            for linea in usuario.splitlines():
                m = re.match(r"id=(\w+) \|", linea)
                if not m:
                    continue
                bajo = linea.lower()
                if "fed" in bajo or "federal reserve" in bajo:
                    elegidas.append({"id": m.group(1), "impacto": 9, "categoria": "ECONOMÍA", "urgente": True,
                                     "motivo": "decisión de tasas", "_detalle": " — " in linea})
                elif "bcra" in bajo:
                    elegidas.append({"id": m.group(1), "impacto": 8, "categoria": "ARGENTINA", "urgente": False, "motivo": "reservas"})
                elif "etf" in bajo:
                    elegidas.append({"id": m.group(1), "impacto": 7, "categoria": "CRIPTO", "urgente": False, "motivo": "salidas"})
                elif "binance" in bajo:
                    elegidas.append({"id": m.group(1), "impacto": 7, "categoria": "EXCHANGES", "urgente": False, "motivo": "marca con foco"})
                elif "mercado libre" in bajo:
                    elegidas.append({"id": m.group(1), "impacto": 6, "categoria": "EMPRESAS", "urgente": False, "motivo": "interesante, no imprescindible"})
            # Como pide la consigna: si dos candidatos cuentan lo mismo (la Fed en inglés y en español), uno solo.
            # ...el que trae más detalle.
            de_la_fed = sorted((e for e in elegidas if e["categoria"] == "ECONOMÍA"), key=lambda e: not e["_detalle"])
            elegidas = [e for e in elegidas if e["categoria"] != "ECONOMÍA"] + de_la_fed[:1]
            elegidas.sort(key=lambda e: -e["impacto"])
            contenido = json.dumps({"elegidas": elegidas[:5]})
            contenido = self.alterar.get("elegir", lambda s: s)(contenido)
        elif "redactor" in sistema:
            if "ahorro" in usuario.lower():
                post = {
                    "titular": "Binance lanza un producto de ahorro con 12% anual garantizado", "titular_imagen": "Binance lanza un producto de ahorro",
                    "resumen": "Binance presentó un producto de ahorro en dólares digitales con 12% anual garantizado.",
                    "dato_clave": "Ofrece 12% anual.", "por_que_importa": "Los productos de ahorro compiten con los plazos fijos.",
                    "pregunta": "¿Lo usarías?", "bandera": "", "tono": "neutral", "fuente": "CoinDesk", "fotos": ["Binance logo"],
                    "llamado": True, "para_la_marca": "buena",
                }
            elif "Federal Reserve" in usuario or "La Fed" in usuario:
                post = {
                    "titular": "La Fed recortó la tasa de interés 25 puntos básicos", "titular_imagen": "La Fed bajó la tasa 25 puntos básicos",
                    "resumen": "La Reserva Federal bajó la tasa de referencia 25 puntos básicos. El comité llevó el rango objetivo a 3,50 por ciento.",
                    "dato_clave": "El recorte fue de 25 puntos básicos.", "por_que_importa": "Una tasa más baja suele abaratar el crédito y favorecer a los activos de riesgo.",
                    "pregunta": "¿Vos esperabas el recorte?", "bandera": "🇺🇸", "tono": "alcista", "fuente": "Reserva Federal (Fed)",
                    "fotos": ["Jerome Powell", "Federal Reserve building", "US dollar banknotes"],
                    "escena": "a central bank building at dusk",
                }
            elif "Binance" in usuario:
                malo = "hackea" in usuario.lower()
                post = {
                    "titular": "Hackean a Binance y suspende los retiros" if malo else "Binance lanza una tarjeta física para pagar con cripto",
                    "titular_imagen": "Hackean a Binance" if malo else "Binance lanza su tarjeta física",
                    "resumen": "Binance informó un ataque y frenó los retiros." if malo else "Binance presentó una tarjeta física para pagar con criptomonedas en comercios.",
                    "dato_clave": "Los retiros están frenados." if malo else "La tarjeta ya se puede pedir desde la app.",
                    "por_que_importa": "Un ataque a un exchange grande suele golpear la confianza." if malo else "Pagar con cripto en cualquier comercio puede acercar su uso al día a día.",
                    "pregunta": "¿Vos la pedirías?", "bandera": "", "tono": "neutral", "fuente": "Reuters",
                    "fotos": ["Binance logo", "Changpeng Zhao"], "escena": "a payment card on a shop counter",
                    # La IA de mentira SIEMPRE dice que es buena y que va el llamado: el freno lo tiene que poner el programa.
                    "llamado": True, "para_la_marca": self.para_la_marca,
                }
            elif "Mercado Libre" in usuario:
                post = {
                    "titular": "Mercado Libre anuncia una inversión récord en Brasil", "titular_imagen": "Mercado Libre invierte en Brasil",
                    "resumen": "Mercado Libre anunció una inversión récord en Brasil para ampliar su red de envíos.",
                    "dato_clave": "La inversión apunta a la red de envíos.", "por_que_importa": "Más inversión suele anticipar más competencia en el comercio online.",
                    "pregunta": "¿Comprás por Mercado Libre?", "bandera": "🇧🇷", "tono": "neutral", "fuente": "CoinDesk",
                    "fotos": ["Mercado Libre logo"], "escena": "parcels on a warehouse conveyor belt",
                }
            elif "BCRA" in usuario:
                post = {
                    "titular": "El BCRA compró USD 150 millones y las reservas pasan los USD 40.000 millones", "titular_imagen": "El BCRA compró USD 150 millones",
                    "resumen": "El Banco Central sumó USD 150 millones en la rueda de hoy. Las reservas brutas cerraron en USD 40.120 millones.",
                    "dato_clave": "Las reservas brutas cerraron en USD 40.120 millones.", "por_que_importa": "Más reservas suelen dar margen para sostener el tipo de cambio.",
                    "pregunta": "¿Te parece que alcanza?", "bandera": "🇦🇷", "tono": "alcista", "fuente": "Ámbito.com",
                    "fotos": ["Banco Central de la República Argentina", "Buenos Aires financial district", "US dollar banknotes"],
                }
            else:
                post = {
                    "titular": "Los ETF de Bitcoin sufren la mayor salida de fondos en meses", "titular_imagen": "Fuerte salida de fondos de los ETF de Bitcoin",
                    "resumen": "Los ETF de Bitcoin al contado registraron salidas netas por 1,2 mil millones de dólares esta semana.",
                    "dato_clave": "Salieron 1,2 mil millones de dólares en una semana.", "por_que_importa": "Las salidas de los ETF suelen presionar el precio a la baja.",
                    "pregunta": "¿Te sorprende? Votá ✅ o ❌", "bandera": "", "tono": "bajista", "fuente": "CoinDesk",
                    "fotos": ["Bitcoin logo", "New York Stock Exchange", "Bitcoin"],
                }
            # Con un número muy alto la IA "insiste" aunque se la corrija.
            if self.cifra_inventada > 0 and ("ATENCIÓN" not in usuario or self.cifra_inventada > 50):
                self.cifra_inventada -= 1
                post["resumen"] += " Es el recorte número 7 del año."
            contenido = "```json\n" + json.dumps(post, ensure_ascii=False) + "\n```"
            contenido = self.alterar.get("redactar", lambda s: s)(contenido)
        elif "revisor de publicidad" in sistema:
            texto_a_revisar = usuario.split("TEXTO A REVISAR:")[-1]
            self.revisiones.append(texto_a_revisar)
            sutil = "vas a notar la diferencia en tu billetera" in texto_a_revisar
            contenido = json.dumps({"promete": bool(self.juez_promete or sutil), "frase": "vas a notar la diferencia en tu billetera" if sutil else ""})
        elif "publicaciones de venta" in sistema:
            promo = {
                "titular": "Mis bots operan solos y tu plata nunca sale de tu cuenta",
                "titular_imagen": "Bots que operan por vos",
                "texto": "Armo mis propios bots en Pionex y los podés copiar sin configurar nada. La plata queda siempre en tu cuenta.",
                "puntos": ["Sin configurar nada", "Tu plata queda en tu cuenta", "Operan las 24 horas"],
            }
            if "bienvenida" in usuario.lower():
                promo = {
                    "titular": "Pionex tiene una bienvenida para los que recién llegan",
                    "titular_imagen": "Bienvenida de Pionex en 3 pasos",
                    "texto": "Si te registrás con mi link entrás en la campaña de bienvenida. El bono se desbloquea en 3 pasos.",
                    "puntos": ["Verificás tu cuenta", "Hacés tu primer depósito", "Abrís tu propio bot"],
                }
            if "labitconf" in usuario.lower():
                promo = {
                    "titular": "LABITCONF 2026 con 40% OFF para la comunidad",
                    "titular_imagen": "40% OFF en LABITCONF 2026",
                    "texto": "Somos Media Partner de LABITCONF 2026. Con el código COMUNIDADINVERSORES40 tenés 40% OFF en la entrada GENERAL. Es el 30 y 31 de octubre en Costa Salguero.",
                    "puntos": ["Código COMUNIDADINVERSORES40", "Válido para los dos días", "Hasta 100 usos"],
                }
            elif "9.300" in usuario:
                promo = {
                    "titular": "Probé más de 9.300 combinaciones y solo pasaron 2 pares",
                    "titular_imagen": "9.300 combinaciones, 2 pares",
                    "texto": "En el video te muestro el backtest de más de 4 años sobre AUD/CAD y EUR/AUD, con el drawdown y los períodos negativos. No es solo una curva linda.",
                    "puntos": ["Cómo funciona el sistema", "Cómo seguir las señales", "Los datos del backtest"],
                }
            elif "señales de trading en exness" in usuario.lower():
                promo = {
                    "titular": "Señales para AUD/CAD y EUR/AUD en un canal gratuito",
                    "titular_imagen": "Señales de trading en Exness",
                    "texto": "El sistema te indica cuándo entrar, dónde poner el Stop Loss y el Take Profit, y cuándo pasar a Break Even. Mirá el dossier: muestra la metodología y los riesgos.",
                    "puntos": ["Canal gratuito", "Dos pares: AUD/CAD y EUR/AUD", "20% de devolución de comisiones"],
                }
            if "tarjeta" in usuario.lower():
                promo = {
                    "titular": "La tarjeta de Binance ya se puede pedir en Argentina",
                    "titular_imagen": "Pagá con cripto en cualquier comercio",
                    "texto": "Se pide gratis desde la app y sirve en cualquier comercio que acepte tarjetas. Hasta el 31 de diciembre devuelve 3% de cada compra en USDC.",
                    "puntos": ["Sin costo de emisión ni de envío", "3% de reintegro en USDC", "Tope de 100 USDC por mes"],
                }
            if self.promo_tramposa > 0 and ("ATENCIÓN" not in usuario or self.promo_tramposa > 50):
                self.promo_tramposa -= 1
                promo["texto"] += " Ganancias aseguradas del 20% por mes."
            if self.promo_sutil > 0 and ("ATENCIÓN" not in usuario or self.promo_sutil > 50):
                self.promo_sutil -= 1
                promo["texto"] += " En poco tiempo vas a notar la diferencia en tu billetera."
            contenido = json.dumps(promo, ensure_ascii=False)
            contenido = self.alterar.get("promo", lambda s: s)(contenido)
        elif "Elegís la imagen" in sistema:
            contenido = json.dumps({"elegida": self.foto_elegida})
        else:
            contenido = "{}"

        if host == "api.anthropic.com":
            return self._r(pedido, 200, {"content": [{"type": "text", "text": contenido}]})
        return self._r(pedido, 200, {"choices": [{"message": {"role": "assistant", "content": contenido}}]})

    # ------------------------------------------------------- imágenes con IA
    def _imagen_ia(self, pedido) -> requests.Response:
        datos = json.loads(self._cuerpo(pedido))
        assert self._cab(pedido, "x-goog-api-key"), "falta la clave"
        assert datos["response_format"] == {"type": "image", "mime_type": "image/jpeg", "aspect_ratio": "1:1", "image_size": "1K"}
        self.imagenes_ia.append(datos)
        return self._r(pedido, 200, {
            "id": "interaccion-1", "status": "completed",
            "steps": [{"type": "model_output", "content": [
                {"type": "text", "text": "Listo"},
                {"type": "image", "mime_type": "image/jpeg", "data": base64.b64encode(_jpg(1024, 1024)).decode()},
            ]}],
        })

    # ------------------------------------------------------------- Wikimedia
    def _wiki(self, pedido, u, host: str) -> requests.Response:
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        assert "NoticiasCI-Bot" in self._cab(pedido, "User-Agent"), "Wikimedia exige identificarse"
        assert q.get("format") == "json" and q.get("formatversion") == "2"

        def info(nombre: str, mime="image/jpeg", licencia="Public domain", ancho=3000, alto=2000) -> dict:
            return {
                "url": f"https://upload.wikimedia.org/wikipedia/commons/a/ab/{nombre}",
                "thumburl": f"https://upload.wikimedia.org/wikipedia/commons/thumb/a/ab/{nombre}/1280px-{nombre}",
                "width": ancho, "height": alto, "mime": mime,
                "extmetadata": {
                    "LicenseShortName": {"value": licencia}, "Artist": {"value": '<a href="//x">Federalreserve</a>'},
                    "ImageDescription": {"value": "<p>Official portrait</p>"}, "DateTimeOriginal": {"value": "2024-05-01 10:00:00"},
                },
            }

        if host.endswith("wikipedia.org"):
            assert q.get("pilicense") == "free"
            if "powell" in q.get("gsrsearch", "").lower():
                return self._r(pedido, 200, {"query": {"pages": [{"pageid": 1, "title": "Jerome Powell", "index": 1, "pageimage": "Jerome_H._Powell.jpg"}]}})
            return self._r(pedido, 200, {"query": {"pages": [{"pageid": 2, "title": "Otra", "index": 1}]}})
        if q.get("titles"):
            nombre = q["titles"].split("File:")[-1]
            return self._r(pedido, 200, {"query": {"pages": [{"title": f"File:{nombre}", "imageinfo": [info(nombre)]}]}})
        busqueda = q.get("gsrsearch", "")
        if "logo" in busqueda.lower():
            return self._r(pedido, 200, {"query": {"pages": [
                {"title": "File:Bitcoin logo.svg", "index": 1, "imageinfo": [info("Bitcoin_logo.svg", "image/svg+xml", "CC0", 512, 512)]},
            ]}})
        return self._r(pedido, 200, {"query": {"pages": [
            {"title": "File:Foto real.jpg", "index": 2, "imageinfo": [info("Foto_real.jpg", licencia="CC BY-SA 4.0")]},
            {"title": "File:Signature of someone.png", "index": 1, "imageinfo": [info("Signature.png", "image/png")]},
            {"title": "File:Foto no comercial.jpg", "index": 3, "imageinfo": [info("NC.jpg", licencia="CC BY-NC 2.0")]},
            {"title": "File:Foto chiquita.jpg", "index": 4, "imageinfo": [info("Chica.jpg", ancho=300, alto=200)]},
        ]}})
