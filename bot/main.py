"""Una corrida del bot: buscar -> elegir -> redactar -> imagen -> publicar.

Modos (variable MODO):
  real    publica en Telegram y X (solo si 'encendido: true' en ajustes.yaml)
  prueba  hace todo igual pero NO publica: manda la muestra al chat de prueba
          y deja las imágenes en la carpeta salida/
          (con PROMO=1 o PROMO=<id> arma una muestra de una promo, aunque esté en borrador)
  chats   muestra los chats donde está el bot (para averiguar el destino)
"""
from __future__ import annotations

import html
import json
import os
import re
import sys
from datetime import date

from PIL import Image

from . import ia
from .estado import Estado
from .filtro import Grupo, es_mala_noticia, nombra_prohibido, preparar, puntaje, ya_contada
from .fuentes import leer_fuentes
from .imagen import armar_placa, armar_promo, buscar_foto, generar_con_ia, logo_guardado, tal_cual
from .publicar import ErrorPublicacion, claves_x, telegram_chats, telegram_mensaje, telegram_publicar, x_publicar
from .texto import (
    anio_actual, armar_llamado, enlaces_extra, faltantes, faltantes_promo, fuente_limpia, idea_de_imagen, normalizar_post, normalizar_promo,
    numeros_inventados, promesas, promo_telegram, promo_x, texto_revisable, texto_revisable_promo,
    link_en_x, texto_propio_telegram, texto_propio_x, texto_telegram, texto_x, x_largo, x_premium,
)
from .util import (
    RAIZ, a_hora, ahora_utc, cargar_ajustes, configurar_log, cupo_a_esta_hora, en_silencio, log, normalizar, socios_activos,
    verdadero, zona,
)

SALIDA = RAIZ / "salida"
EXTRAS_URGENTES = 2          # cuántas noticias de última hora pueden pasar el tope del día


def fecha_de_vencimiento(promo: dict) -> date | None:
    """La fecha 'vence' de una promo (2026-12-31 o 31/12/2026). None si no tiene o está mal escrita."""
    valor = str(promo.get("vence") or "").strip()[:10]
    try:
        if re.fullmatch(r"\d{1,2}/\d{1,2}/(\d{2}|\d{4})", valor):
            dia, mes, anio = (int(x) for x in valor.split("/"))
            return date(anio + 2000 if anio < 100 else anio, mes, dia)
        return date.fromisoformat(valor)
    except ValueError:
        return None


def promo_vencida(promo: dict, hoy: date) -> bool:
    """Una promo con fecha deja de salir al día siguiente. Si la fecha está mal escrita tampoco sale
    (mejor saltear una promo que anunciar una campaña que ya terminó)."""
    if not str(promo.get("vence") or "").strip():
        return False
    limite = fecha_de_vencimiento(promo)
    return limite is None or hoy > limite


def imagen_propia(ruta: str) -> Image.Image | None:
    """Una imagen hecha a mano (por ejemplo, el flyer de una promo) guardada en el proyecto."""
    if not ruta or not (RAIZ / ruta).exists():
        return None
    try:
        imagen = Image.open(RAIZ / ruta)
        imagen.load()
        return imagen
    except OSError:
        return None


class Corrida:
    def __init__(self, modo: str, cantidad: int, forzar: bool, promo: str = "") -> None:
        self.modo, self.cantidad, self.forzar = modo, cantidad, forzar
        self.promo_pedida = promo.strip()
        self.real = modo == "real"
        self.cfg = cargar_ajustes()
        self.ahora = ahora_utc()
        self.estado = Estado()
        self.token = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
        tg = self.cfg["telegram"]
        self.destino = (os.environ.get("TELEGRAM_DESTINO") or str(tg.get("destino") or "")).strip()
        # Otros lugares donde sale lo mismo (por ejemplo, el tema "Noticias" del grupo).
        extras = tg.get("tambien_en") or []
        extras = [extras] if isinstance(extras, (str, int)) else extras
        self.tambien_en = [str(d).strip() for d in extras if str(d).strip() and str(d).strip() != self.destino]
        self.chat_prueba = (os.environ.get("TELEGRAM_CHAT_PRUEBA") or str(tg.get("chat_prueba") or "")).strip()
        self.claves_x = claves_x()
        # imagen.modo: 'ninguna' = publica solo el texto (la imagen la pone el dueño a mano) · 'placa' = la arma el bot
        self.sin_imagen = str(self.cfg["imagen"].get("modo") or "placa").strip().lower() in ("ninguna", "texto", "no", "false")
        self.cerebro = ia.Cerebro(self.cfg)
        self.canales: list[str] = []
        self.fallas = 0
        self.aviso_fallido = False      # True si hubo un problema y no se le pudo avisar al dueño
        self.informe: dict = {"modo": modo, "cuando": self.ahora.isoformat(), "publicaciones": []}

    # ------------------------------------------------------------ avisos
    def avisar(self, clave: str, mensaje: str, cada_horas: float = 12) -> None:
        """Le avisa al dueño por Telegram (como mucho una vez cada 12 h por cada tipo de problema).
        Si no hay a dónde avisar, la corrida termina marcada como fallida y GitHub manda un mail."""
        log.error("AVISO: %s", mensaje)
        if self.real and not self.estado.puede_avisar(clave, cada_horas):
            return
        if not (self.token and self.chat_prueba):
            self.aviso_fallido = True
            return
        try:
            telegram_mensaje(self.token, self.chat_prueba, f"⚠️ Bot de noticias\n\n{mensaje}")
        except ErrorPublicacion as error:
            self.aviso_fallido = True
            log.error("No pude mandar el aviso: %s", error)

    # ------------------------------------------------- entregar (común a todo)
    def x_largo(self, para_telegram: str, publicidad: bool) -> str:
        """Con X Premium ('premium: true'), en X sale el texto completo, igual que en Telegram."""
        if not x_premium(self.cfg["x"]):
            return ""
        return x_largo(para_telegram, self.cfg["x"], link_en_x(self.cfg["x"], publicidad))

    def entregar(self, numero: int, jpg: bytes | None, para_telegram: str, para_x: str, registro: dict,
                 publicidad: bool = False, con_ia: bool = False, original: str = "") -> str:
        """Publica (o manda la muestra), con imagen o solo texto (jpg=None). Devuelve 'ok', 'reintentar'
        (no salió y es seguro probar de nuevo más tarde) o 'perdida' (no salió y no se puede
        reintentar sin riesgo de duplicar)."""
        SALIDA.mkdir(exist_ok=True)
        if jpg is not None:
            (SALIDA / f"{numero:02d}.jpg").write_bytes(jpg)
        (SALIDA / f"{numero:02d}.txt").write_text(
            f"--- TELEGRAM ---\n{re.sub('<[^>]+>', '', para_telegram)}\n\n--- X ---\n{para_x}\n\n--- ORIGEN ---\n{original}\n",
            encoding="utf-8",
        )
        self.informe["publicaciones"].append({**registro, "texto_telegram": para_telegram, "texto_x": para_x})

        if not self.real:
            if self.token and self.chat_prueba:
                try:
                    telegram_publicar(self.token, self.chat_prueba, jpg, para_telegram)
                    telegram_mensaje(self.token, self.chat_prueba,
                                     f"👆 Así saldría en Telegram.\n\nY así saldría en X:\n\n{self.x_largo(para_telegram, publicidad) or para_x}")
                except ErrorPublicacion as error:
                    log.error("  no pude mandar la muestra: %s", error)
                    self.fallas += 1
            return "ok"

        errores: list[ErrorPublicacion] = []
        if "telegram" in self.canales:
            try:
                registro["telegram"] = telegram_publicar(self.token, self.destino, jpg, para_telegram)
                for otro in self.tambien_en:
                    # Si el canal ya salió, una falla acá solo se avisa: reintentar duplicaría el canal.
                    try:
                        telegram_publicar(self.token, otro, jpg, para_telegram)
                    except ErrorPublicacion as error:
                        self.fallas += 1
                        self.avisar(f"tg_otro_{otro}", f"Salió en el canal pero no en {otro}: {error}")
                log.info("  publicado en Telegram")
            except ErrorPublicacion as error:
                errores.append(error)
        if "x" in self.canales:
            try:
                avisar_publicidad = publicidad and verdadero(self.cfg["x"].get("marcar_publicidad", True))
                largo = self.x_largo(para_telegram, publicidad)
                registro["x"] = x_publicar(self.claves_x, jpg, largo or para_x, publicidad=avisar_publicidad, hecha_con_ia=con_ia,
                                           texto_corto=para_x if largo else "")
                log.info("  publicado en X")
            except ErrorPublicacion as error:
                errores.append(error)
        for error in errores:
            self.fallas += 1
            self.avisar(error.clave, str(error))

        if registro["telegram"] is not None or registro["x"] is not None:
            self.estado.agregar_publicada(registro)
            return "ok"
        # No salió en ningún lado. Se vuelve a intentar más tarde solo si es seguro: si en algún canal
        # la conexión se cortó a mitad de camino, puede haberse publicado igual y repetirlo lo duplicaría.
        if any(e.incierto for e in errores):
            return "perdida"
        return "reintentar" if any(e.reintentar for e in errores) else "perdida"

    # --------------------------------------------------------- una noticia
    def redactar(self, g: Grupo) -> dict | None:
        """Pide el texto a la IA y lo revisa. Si inventa una cifra, se le pide de nuevo; si insiste, se descarta."""
        marca = str((self.cfg["socios"].get(g.socio) or {}).get("nombre", "")) if g.socio else ""
        # Los medios de verdad de esta noticia, con el nombre ya limpio (sin nada que se vuelva link).
        reales = {normalizar(fuente_limpia(f)): fuente_limpia(f) for f in g.fuentes}
        correccion = ""
        for _ in range(2):
            crudo = ia.redactar(self.cerebro, g, correccion, marca)
            post = normalizar_post(crudo, g.categoria, g.principal.fuente)
            dicha = normalizar(post["fuente"])
            if dicha in reales:
                post["fuente"] = reales[dicha]
            else:
                # Nunca una fuente que no esté en la noticia. La IA suele acortar el nombre
                # ("Investing" por "Investing Español"): eso vale; cualquier otra cosa, no.
                parecida = next((limpia for clave, limpia in reales.items() if dicha and dicha in clave.split()), None)
                post["fuente"] = parecida or fuente_limpia(g.principal.fuente)
            falta = faltantes(post)
            if falta:
                correccion = f"en tu intento anterior faltó completar: {', '.join(falta)}. Devolvé el JSON completo."
                continue
            inventados = numeros_inventados(texto_revisable(post), g.texto_para_cifras(), anio_actual(self.ahora))
            if inventados:
                log.warning("  cifras que no están en la noticia original: %s", ", ".join(inventados))
                correccion = (
                    "en tu intento anterior pusiste cifras que NO están en el texto original: "
                    + ", ".join(inventados)
                    + ". Reescribí usando solo cifras que figuren tal cual en el texto original (o ninguna)."
                )
                continue
            if nombra_prohibido(texto_revisable(post), self.cfg["no_nombrar"]):
                return None
            return post
        return None

    def llamado_para(self, g: Grupo, post: dict) -> dict | None:
        """El 'registrate con mi link'. Solo va si la noticia es de un socio, es BUENA para esa marca
        y no habla de ganancias ni rendimientos. Ante cualquier duda, la noticia sale sin llamado."""
        if not g.socio or not post["llamado"] or post["para_la_marca"] != "buena":
            return None
        if post["tono"] == "bajista" or es_mala_noticia(f"{g.titulos()} {post['titular']} {post['resumen']}"):
            return None
        if promesas(texto_revisable(post), g.texto_para_cifras()):
            return None          # "un producto con 12% anual garantizado" no puede salir pegado a tu link
        tope = int(self.cfg["ritmo"].get("max_llamados_por_dia", 2))
        if self.real and self.estado.publicadas_hoy(self.cfg, self.ahora, tipo="noticia", con_llamado=True) >= tope:
            return None
        return armar_llamado(self.cfg["socios"].get(g.socio), self.cfg)

    def imagen_para(self, g: Grupo, post: dict):
        """Logo oficial o foto real; si no hay y está activado, imagen hecha con IA; si no, nada (placa de texto)."""
        busquedas = list(post["fotos"])
        if g.socio:
            nombre = str((self.cfg["socios"].get(g.socio) or {}).get("nombre", g.socio))
            busquedas.insert(0, f"{nombre} logo")
        foto = None
        try:
            foto = buscar_foto(
                busquedas, post["titular"], self.estado.foto_usada,
                elegir=lambda titular, opciones: ia.elegir_foto(self.cerebro, titular, opciones),
            )
        except Exception as error:      # la imagen nunca debe frenar la publicación
            log.warning("  búsqueda de imagen falló (%s)", type(error).__name__)
        opciones_ia = self.cfg["imagen_ia"]
        clave = (os.environ.get("GEMINI_API_KEY") or "").strip()
        if foto is None and verdadero(opciones_ia.get("activa")) and clave:
            foto = generar_con_ia(post["escena"], clave, str(opciones_ia.get("modelo") or "gemini-3.1-flash-lite-image"))
        return foto

    def procesar(self, g: Grupo, numero: int) -> str:
        """Devuelve 'ok', 'descartar' (el texto no pasó los controles), 'reintentar' o 'perdida'
        (estas dos últimas: no se pudo publicar; ver entregar)."""
        log.info("Noticia %d [impacto %d, %s]: %s", numero, g.impacto, g.categoria, g.principal.titulo[:90])
        post = self.redactar(g)
        if post is None:
            log.warning("  descartada: el texto no pasó los controles")
            return "descartar"
        llamado = self.llamado_para(g, post)
        if self.sin_imagen:
            foto, jpg = None, None
            log.info("  sin imagen (modo solo texto)%s", " · con llamado a registrarse" if llamado else "")
        else:
            foto = self.imagen_para(g, post)
            log.info("  imagen: %s%s", foto.titulo if foto else "ninguna (placa de texto)", " · con llamado a registrarse" if llamado else "")
            jpg = armar_placa(post, foto, self.cfg, self.ahora, self.estado.proxima_variante(), g.urgente)
        para_telegram = texto_telegram(post, g.urgente, llamado)
        para_x = texto_x(post, g.urgente, llamado, self.cfg["x"])
        llamado_en_x = bool(llamado) and llamado["texto"] in para_x      # en X puede no entrar con su aviso
        registro = {
            "fecha": self.ahora.isoformat(), "tipo": "noticia", "titular": post["titular"], "categoria": g.categoria,
            "impacto": g.impacto, "fuente": post["fuente"], "foto": foto.titulo if foto else "", "socio": g.socio,
            "llamado": bool(llamado), "titulos_originales": [m.titulo for m in g.miembros[:3]], "telegram": None, "x": None,
        }
        resultado = self.entregar(
            numero, jpg, para_telegram, para_x, registro,
            publicidad=llamado_en_x, con_ia=bool(foto and foto.origen == "ia"), original=g.texto_fuente(),
        )
        if resultado == "ok" and self.real and foto and foto.origen == "wikimedia":
            self.estado.marcar_foto(foto.titulo)
        if resultado == "ok" and self.sin_imagen:
            self.mandar_idea(post, g, registro)
        return resultado

    def mandar_idea(self, post: dict, g: Grupo, registro: dict) -> None:
        """Cuando el bot publica solo texto, le manda al dueño por privado un pedido listo para pegar
        en ChatGPT y hacer la imagen de esa noticia."""
        if not (verdadero(self.cfg["imagen"].get("idea_para_chatgpt", True)) and self.token and self.chat_prueba):
            return
        socio = str((self.cfg["socios"].get(g.socio) or {}).get("nombre", "")) if g.socio else ""
        pedido = idea_de_imagen(post, g.urgente, str(self.cfg["marca"].get("nombre", "")), socio)
        donde = ""
        if self.destino.startswith("@") and registro.get("telegram"):
            donde = f"\nLa publicación: https://t.me/{self.destino[1:]}/{registro['telegram']}"
        mensaje = (
            f"🖼 <b>Imagen para esta noticia</b>\n{html.escape(post['titular'], quote=False)}{donde}\n\n"
            f"Tocá el texto de abajo para copiarlo y pegalo en ChatGPT:\n\n<code>{html.escape(pedido, quote=False)}</code>"
        )
        try:
            telegram_mensaje(self.token, self.chat_prueba, mensaje, html=True)
        except ErrorPublicacion as error:           # la noticia ya salió: esto es solo una ayuda
            log.warning("  no pude mandar la idea para la imagen: %s", error)

    # ------------------------------------------------------------ una promo
    def promos_activas(self) -> list[dict]:
        """Las promos que pueden salir: aprobadas ('activa: true'), con datos, sin vencer y, si son
        de una marca, que esa marca se esté promocionando ('activo' en socios)."""
        hoy = self.ahora.astimezone(zona(self.cfg)).date()
        activos = socios_activos(self.cfg)
        return [
            p for p in self.cfg["promos"]
            if p.get("id") and verdadero(p.get("activa")) and str(p.get("datos") or p.get("texto_fijo") or "").strip()
            and not promo_vencida(p, hoy)
            and (not p.get("socio") or str(p["socio"]) in activos)
        ]

    def avisar_vencidas(self) -> None:
        """Si una promo aprobada ya venció, deja de salir sola y se le avisa al dueño (una vez por semana)."""
        hoy = self.ahora.astimezone(zona(self.cfg)).date()
        for p in self.cfg["promos"]:
            if p.get("id") and verdadero(p.get("activa")) and promo_vencida(p, hoy):
                limite = fecha_de_vencimiento(p)
                detalle = f"venció el {limite:%d/%m/%Y}" if limite else f"tiene mal escrita la fecha 'vence' ({p.get('vence')})"
                self.avisar(
                    f"promo_vencida_{p['id']}",
                    f"La promo «{p.get('nombre') or p['id']}» {detalle} y dejó de salir. "
                    "Si la campaña sigue, corregí la fecha en ajustes.yaml; si terminó, ponele activa: false.",
                    cada_horas=24 * 7,
                )

    def promo_que_toca(self) -> dict | None:
        """Devuelve la promo que hay que publicar ahora, o None si todavía no es la hora."""
        r = self.cfg["ritmo"]
        cupo = int(r.get("promos_por_dia", 0) or 0)
        activas = self.promos_activas()
        if cupo <= 0 or not activas or en_silencio(self.cfg, self.ahora):
            return None
        if self.estado.publicadas_hoy(self.cfg, self.ahora, tipo="promo") >= cupo:
            return None
        local = self.ahora.astimezone(zona(self.cfg))
        pasados = [h for h in sorted(a_hora(h) for h in (r.get("horarios_promos") or [])) if h <= local.time()]
        if not pasados:
            return None                              # todavía no llegó el primer horario de promo
        ultima = self.estado.ultima_fecha("promo")
        if ultima is not None:
            ultima_local = ultima.astimezone(zona(self.cfg))
            if ultima_local.date() == local.date() and ultima_local.time() >= pasados[-1]:
                return None                          # la promo de este horario ya salió
            if (self.ahora - ultima).total_seconds() < float(r.get("horas_entre_promos", 3)) * 3600:
                return None                          # nunca dos promos pegadas
        return self.estado.promo_que_toca(activas)

    def procesar_promo(self, promo: dict, numero: int) -> str:
        """Devuelve 'ok', 'rechazada' (el texto no pasó los controles), 'reintentar' o 'perdida'
        (estas dos últimas: no se pudo publicar; ver entregar)."""
        nombre = str(promo.get("nombre") or promo.get("id"))
        log.info("Promo %d: %s", numero, nombre)
        propio = str(promo.get("texto_fijo") or "").strip()
        if propio:
            return self.promo_con_texto_propio(promo, propio, numero)
        socio = self.cfg["socios"].get(str(promo.get("socio") or ""), {})
        llamado = armar_llamado(
            socio, self.cfg, str(promo.get("llamado") or ""), str(promo.get("enlace") or ""), promo.get("aviso"),
            str(promo.get("texto_sin_link") or ""), promo.get("paso_2"),
        )
        if llamado is None:
            self.avisar(f"promo_{promo.get('id')}", f"La promo «{nombre}» no salió: le falta el texto del llamado o el link. Revisala en ajustes.yaml.")
            return "rechazada"
        datos = str(promo.get("datos") or "").strip()
        dato_fijo = str(promo.get("dato_fijo") or "")       # lo que no puede faltar (un código de descuento, una fecha)
        referencia = f"{datos} {promo.get('nombre', '')} {llamado['texto']} {llamado['aviso']}"
        vuelta = int(self.estado.datos.get("promos_hechas", 0))
        correccion, texto, motivos = "", None, []
        for _ in range(2):
            crudo = ia.redactar_promo(self.cerebro, promo, str(socio.get("nombre", "")), vuelta, correccion)
            candidato = normalizar_promo(crudo)
            revisable = texto_revisable_promo(candidato)
            falta = faltantes_promo(candidato)
            inventados = numeros_inventados(revisable, referencia, anio_actual(self.ahora))
            malas = promesas(revisable, datos)
            if not (falta or inventados or malas):
                # Segunda revisión, hecha por la IA con una sola consigna: ¿esto promete ganancias?
                # (La lista de palabras prohibidas no puede adivinar todas las formas de decirlo.)
                promete, frase = ia.juzgar_promo(self.cerebro, revisable, datos)
                if not promete:
                    texto = candidato
                    break
                malas = [frase or "el revisor la leyó como una promesa"]
            log.warning("  la promo no pasó el control (falta: %s · cifras: %s · promesas: %s)", falta, inventados, malas)
            correccion, motivos = "tu intento anterior no sirve.", []
            if falta:
                correccion += f" Faltó completar: {', '.join(falta)}."
                motivos.append("quedó incompleta")
            if inventados:
                correccion += f" Pusiste cifras que no están en los DATOS: {', '.join(inventados)}."
                motivos.append(f"puso cifras que no están en los datos ({', '.join(inventados)})")
            if malas:
                correccion += f" Sonaba a promesa de ganancia o de falta de riesgo: {', '.join(malas)}. Contá qué es y cómo funciona, sin hablar de resultados."
                motivos.append(f"sonaba a promesa de ganancia ({', '.join(malas)})")
        if texto is None:
            self.avisar(
                f"promo_{promo.get('id')}",
                f"La promo «{nombre}» no salió: la IA la escribió dos veces y las dos veces {' y '.join(motivos)}. "
                "No se publicó nada de eso. Si se repite, revisá el texto de 'datos' de esa promo en ajustes.yaml.",
            )
            return "rechazada"

        ruta = str(promo.get("imagen") or "").strip()
        imagen = imagen_propia(ruta)
        etiqueta = str(promo.get("etiqueta") or "PROMO")
        emoji = str(promo.get("emoji") or ("🤖" if "HERRAMIENTA" in etiqueta.upper() else "🎁"))
        if self.sin_imagen:
            # Modo solo texto: si la promo tiene una imagen propia (hecha a mano), va tal cual; si no, sin imagen.
            jpg = tal_cual(imagen) if imagen is not None else None
        else:
            # Si la promo es de una marca y su logo oficial está guardado, va en la placa.
            logo = logo_guardado(f"{socio.get('nombre')} logo") if socio.get("nombre") else None
            jpg = armar_promo(texto, etiqueta, llamado["texto"], llamado["aviso"], self.cfg, self.ahora, imagen,
                              logo.imagen if logo else None)
        registro = {
            "fecha": self.ahora.isoformat(), "tipo": "promo", "promo_id": str(promo.get("id")), "titular": texto["titular"],
            "categoria": "PROMO", "impacto": 0, "fuente": "", "foto": ruta, "socio": str(promo.get("socio") or ""),
            "llamado": True, "titulos_originales": [], "telegram": None, "x": None,
        }
        resultado = self.entregar(
            numero, jpg, promo_telegram(texto, llamado, emoji, enlaces_extra(promo), dato_fijo),
            promo_x(texto, llamado, self.cfg["x"], emoji, dato_fijo), registro,
            publicidad=True, original=datos,
        )
        if resultado == "ok" and self.real:
            self.estado.marcar_promo(str(promo.get("id")), self.ahora)
        return resultado

    def promo_con_texto_propio(self, promo: dict, propio: str, numero: int) -> str:
        """Promo con el texto que escribió Eze: sale tal cual (la IA no lo toca), con su imagen si tiene."""
        ruta = str(promo.get("imagen") or "").strip()
        imagen = imagen_propia(ruta)
        if ruta and imagen is None:
            self.avisar(f"promo_imagen_{promo.get('id')}", f"La promo «{promo.get('nombre') or promo.get('id')}» salió sin imagen: no encontré {ruta}.")
        jpg = tal_cual(imagen) if imagen is not None else None
        para_telegram = texto_propio_telegram(propio)
        para_x = texto_propio_x(str(promo.get("texto_x") or ""), propio, self.cfg["x"])
        primera = next((l.strip() for l in re.sub(r"<[^>]+>", "", para_telegram).splitlines() if l.strip()), "")
        registro = {
            "fecha": self.ahora.isoformat(), "tipo": "promo", "promo_id": str(promo.get("id")), "titular": html.unescape(primera)[:200],
            "categoria": "PROMO", "impacto": 0, "fuente": "", "foto": ruta, "socio": str(promo.get("socio") or ""),
            "llamado": True, "titulos_originales": [], "telegram": None, "x": None,
        }
        resultado = self.entregar(numero, jpg, para_telegram, para_x, registro, publicidad=True, original=propio)
        if resultado == "ok" and self.real:
            self.estado.marcar_promo(str(promo.get("id")), self.ahora)
        return resultado

    # ----------------------------------------------------------------- todo
    def recuperar_memoria(self) -> int:
        """La memoria existía pero no se pudo leer. Publicar a ciegas podría repetir noticias, así que
        esta corrida no publica nada: anota lo que ya está dando vueltas, avisa y sigue la próxima hora."""
        noticias, _ = leer_fuentes(self.cfg, self.ahora)
        grupos, _ = preparar(noticias, self.cfg, self.ahora)
        self.estado.marcar_evaluadas([g.id for g in grupos])
        self.estado.guardar()
        self.fallas += 1
        self.avisar(
            "memoria_rota",
            "No pude leer la memoria del bot (el archivo estaba dañado). Para no repetir publicaciones, en esta "
            "corrida no publiqué nada: anoté las noticias que ya estaban dando vueltas y sigo normalmente desde "
            "la próxima hora. Por hoy puede salir alguna publicación de más.",
        )
        return self.cerrar(0)

    def correr(self) -> int:
        cfg, r = self.cfg, self.cfg["ritmo"]
        if self.modo == "chats":
            return self.mostrar_chats()

        tope = int(r.get("max_por_dia", 10))
        hoy = promos_hoy = 0
        if self.real:
            if not verdadero(cfg.get("encendido")):
                log.info("El bot está apagado (encendido: false en ajustes.yaml). No hago nada.")
                return 0
            if verdadero(cfg["telegram"].get("publicar")) and self.token and self.destino:
                self.canales.append("telegram")
            if verdadero(cfg["x"].get("publicar")) and self.claves_x:
                self.canales.append("x")
            if not self.canales:
                self.avisar("sin_canales", "No tengo dónde publicar: falta cargar las llaves de Telegram o de X, o el destino de Telegram.")
                return 1
            if self.estado.rota:
                return self.recuperar_memoria()
            hoy = self.estado.publicadas_hoy(cfg, self.ahora)
            promos_hoy = self.estado.publicadas_hoy(cfg, self.ahora, tipo="promo")
            if hoy >= tope + EXTRAS_URGENTES:
                log.info("Ya se publicaron %d hoy (tope diario). Hasta mañana.", hoy)
                return 0
            minutos = self.estado.minutos_desde_ultima(self.ahora)
            if minutos is not None and minutos < float(r.get("minutos_entre_publicaciones", 40)) and not self.forzar:
                log.info("La última publicación fue hace %d min. Espero.", minutos)
                return 0
            self.cantidad = 1

        if not self.cerebro.disponible:
            self.avisar("sin_ia", "Falta la clave de la IA que redacta (GEMINI_API_KEY). Sin eso no puedo escribir los textos.")
            return 1

        # --- muestra de una promo (solo en modo prueba)
        if not self.real and self.promo_pedida:
            con_datos = [p for p in cfg["promos"] if p.get("id") and str(p.get("datos") or p.get("texto_fijo") or "").strip()]
            elegida = next((p for p in con_datos if str(p["id"]) == self.promo_pedida), con_datos[0] if con_datos else None)
            if elegida is None:
                log.error("No hay ninguna promo con datos o texto cargados en ajustes.yaml.")
                return 1
            try:
                resultado = self.procesar_promo(elegida, 1)
            except ia.SinCerebro as error:
                self.avisar("ia_caida", f"La IA no respondió y no pude armar la muestra de la promo. ({str(error)[:200]})")
                resultado = "sin_ia"
            if resultado != "ok":
                self.fallas += 1             # no hay muestra para ver: la corrida queda marcada como fallida
            return self.cerrar(1 if resultado == "ok" else 0)

        if self.real:
            self.avisar_vencidas()
        promo = self.promo_que_toca() if self.real and hoy < tope else None

        noticias, salud = leer_fuentes(cfg, self.ahora)
        self.informe["fuentes"] = salud
        vivas = sum(1 for s in salud if s["noticias"])
        if vivas < 4:
            self.avisar("pocas_fuentes", f"Solo {vivas} de {len(salud)} fuentes de noticias respondieron. Si sigue así, hay que revisarlas.")
            if not noticias and promo is None:
                return self.cerrar(0) or 1

        grupos, cuenta = preparar(noticias, cfg, self.ahora)
        self.informe["conteo"] = cuenta
        recientes = self.estado.titulos_recientes(self.ahora)
        pendientes = self.estado.pendientes(cfg, self.ahora) if self.real else []
        en_cola = {g.id for g in pendientes}
        nuevos = [
            g for g in grupos
            if g.id not in en_cola
            and not (self.real and self.estado.fue_evaluada(g.id))
            and not ya_contada(g, recientes)
        ]
        # Lo que quedó en espera se vuelve a mostrar a la IA junto con lo nuevo: así compara todo
        # contra lo último publicado y no repite una noticia que ya salió contada por otro medio.
        pendientes = [g for g in pendientes if not ya_contada(g, recientes)]
        maximo = int(cfg["ia"].get("candidatos_por_corrida", 30))
        lugar_socios = int(cfg["ia"].get("candidatos_de_socios", 6))
        candidatos = list(pendientes)
        for g in nuevos:                 # vienen ordenadas de mayor a menor puntaje
            if len(candidatos) >= maximo:
                break
            if g.socio:
                if lugar_socios <= 0:
                    continue             # los socios salen en muchas notas: no pueden tapar al resto
                lugar_socios -= 1
            candidatos.append(g)
        self.informe["candidatos"] = [
            {"puntaje": puntaje(g, cfg, self.ahora), "medios": g.fuentes, "socio": g.socio, "titulo": g.principal.titulo}
            for g in candidatos
        ]
        log.info("Noticias: %s. Candidatas para la IA: %d (de ellas, %d estaban en espera)", cuenta, len(candidatos), len(pendientes))

        cola: list[Grupo] = []
        if candidatos:
            try:
                cola = ia.elegir(self.cerebro, candidatos, self.estado.titulares_recientes(self.ahora), cfg, self.ahora)
            except ia.SinCerebro as error:
                self.avisar("ia_caida", f"La IA no respondió, así que esta vez no pude elegir noticias. ({str(error)[:200]})")
                self.fallas += 1
                if self.real:                      # lo que estaba en espera no se pierde
                    self.estado.guardar_pendientes(pendientes)
                    self.estado.guardar()
                return self.cerrar(0)
            if self.real:
                self.estado.marcar_evaluadas([g.id for g in candidatos])
        cola.sort(key=lambda g: (-g.impacto, -g.fecha.timestamp()))

        minimo = int(r.get("impacto_minimo", 7))
        relleno = min(minimo, int(r.get("impacto_de_relleno") or minimo))
        urgente = int(r.get("impacto_urgente", 9))
        hay_urgente = any(g.impacto >= urgente for g in cola)

        # --- ¿toca una promo? Sale la promo, salvo que haya una noticia de última hora.
        if promo is not None and not hay_urgente:
            id_promo = str(promo.get("id"))
            try:
                resultado = self.procesar_promo(promo, 1)
            except ia.SinCerebro as error:
                self.avisar("ia_caida", f"La IA no respondió y no pude armar la promo. ({str(error)[:200]})")
                self.fallas += 1
                resultado = "sin_ia"
            except Exception as error:            # una promo con problemas no debe frenar al resto
                log.exception("  error inesperado armando la promo")
                self.avisar("error_inesperado", f"Tuve un error inesperado armando la promo «{promo.get('nombre') or id_promo}» ({type(error).__name__}). La salteé y seguí con las noticias.")
                self.fallas += 1
                resultado = "rechazada"
            if resultado == "ok":
                self.estado.guardar_pendientes([g for g in cola if g.impacto >= minimo])
                self.estado.guardar()
                return self.cerrar(1)
            # No salió. Esa promo pasa al final de la fila: la próxima vez se prueba con otra, así una
            # promo con problemas nunca traba a las demás ni deja al canal sin publicar.
            self.estado.marcar_promo(id_promo, self.ahora)
            if resultado in ("reintentar", "perdida"):
                # No se pudo publicar en ningún canal: con una noticia pasaría lo mismo. Hasta la próxima hora.
                self.estado.guardar_pendientes([g for g in cola if g.impacto >= minimo])
                self.estado.guardar()
                return self.cerrar(0)
            # El texto no pasó los controles (o la IA no respondió): este lugar lo ocupa una noticia.

        # --- noticias. Si es de madrugada, o el día ya va adelantado, solo pasa lo urgente.
        umbral = minimo
        if self.real and self.forzar:
            # Forzar = "publicá ahora": saltea la espera, el reparto del día y el horario de silencio,
            # pero nunca el tope de publicaciones del día.
            if hoy >= tope:
                umbral = urgente
                log.info("Ya van %d publicaciones (el tope del día): aunque se fuerce, solo sale lo de impacto %d o más.", hoy, urgente)
        elif self.real:
            lugar_para_promos = min(int(r.get("promos_por_dia", 0) or 0), len(r.get("horarios_promos") or [])) if self.promos_activas() else 0
            noticias_hoy = hoy - promos_hoy
            cupo = cupo_a_esta_hora(cfg, self.ahora)
            motivo = ""
            if en_silencio(cfg, self.ahora):
                motivo = "es horario de silencio"
            elif hoy >= cupo:
                motivo = f"ya van {hoy} publicaciones y a esta hora corresponden {cupo}"
            elif noticias_hoy >= tope - lugar_para_promos:
                motivo = "lo que queda del día está reservado para las promos"
            if motivo:
                umbral = urgente
                log.info("Solo sale lo de impacto %d o más: %s.", urgente, motivo)
            elif cupo - hoy >= 2 and relleno < minimo:
                # El día viene flojo: para acercarse a las publicaciones del día, se acepta una noticia
                # un escalón más abajo (siempre sale primero la de más impacto que haya).
                umbral = relleno
                log.info("El día viene atrasado (van %d y a esta hora corresponden %d): también sale lo de impacto %d.", hoy, cupo, relleno)
        publicables = [g for g in cola if g.impacto >= umbral]
        guardadas = [g for g in cola if minimo <= g.impacto < umbral]

        if not self.real and self.forzar and len(publicables) < self.cantidad:
            # En una prueba forzada siempre se arma algo para ver, aunque no haya nada fuerte.
            for g in candidatos:
                if len(publicables) >= self.cantidad:
                    break
                if g not in publicables:
                    g.categoria = g.categoria or g.principal.tema or str(cfg["categorias"][0])
                    publicables.append(g)

        hechas, sobrantes = 0, []
        for i, g in enumerate(publicables):
            if hechas >= self.cantidad:
                sobrantes.append(g)
                continue
            try:
                resultado = self.procesar(g, hechas + 1)
            except ia.SinCerebro as error:
                self.avisar("ia_caida", f"La IA dejó de responder en medio de la corrida. ({str(error)[:200]})")
                self.fallas += 1
                sobrantes.extend(publicables[i:])       # esta y las que faltaban quedan para la próxima hora
                break
            except Exception as error:                  # una noticia con problemas no debe frenar a las demás
                log.exception("  error inesperado con esta noticia; sigo con la próxima")
                self.fallas += 1
                self.avisar("error_inesperado", f"Tuve un error inesperado armando una publicación ({type(error).__name__}). La salteé y seguí. Si este aviso se repite, hay que revisar el bot.")
                g.intentos += 1
                sobrantes.append(g)                     # se prueba una vez más; a la segunda se descarta
                continue
            if resultado == "ok":
                hechas += 1
                recientes += [m.titulo for m in g.miembros[:3]]
            elif resultado in ("reintentar", "perdida"):
                # No se pudo publicar en ningún canal. Con las demás pasaría lo mismo: esperan a la próxima hora.
                if resultado == "reintentar":
                    g.intentos += 1
                    sobrantes.append(g)
                sobrantes.extend(publicables[i + 1:])
                break

        if self.real:
            self.estado.guardar_pendientes(sobrantes + guardadas)
            self.estado.guardar()
        return self.cerrar(hechas)

    def cerrar(self, hechas: int) -> int:
        """Deja el informe de la corrida y decide si terminó bien o mal."""
        self.informe.update({"hechas": hechas, "llamadas_ia": self.cerebro.llamadas, "fallas": self.fallas})
        SALIDA.mkdir(exist_ok=True)
        (SALIDA / "reporte.json").write_text(json.dumps(self.informe, ensure_ascii=False, indent=1), encoding="utf-8")
        accion = "Publicadas" if self.real else "Muestras armadas"
        log.info("%s: %d. Llamadas a la IA: %d. Fallas: %d.", accion, hechas, self.cerebro.llamadas, self.fallas)
        # En modo real los problemas se avisan por Telegram; la corrida solo se marca como
        # fallida (y GitHub manda un mail) cuando ese aviso no se pudo entregar.
        if self.real:
            return 1 if self.aviso_fallido else 0
        return 1 if self.fallas and hechas == 0 else 0

    def mostrar_chats(self) -> int:
        if not self.token:
            log.error("Falta TELEGRAM_BOT_TOKEN.")
            return 1
        try:
            chats = telegram_chats(self.token)
        except ErrorPublicacion as error:
            log.error("%s", error)
            return 1
        if not chats:
            log.info("No veo chats todavía. Escribile /start al bot (o agregalo al canal y publicá algo) y probá de nuevo.")
        lineas = []
        for c in chats:
            log.info("CHAT  id=%s  tipo=%s  nombre=%s  usuario=%s", c["id"], c["tipo"], c["nombre"], ("@" + c["usuario"]) if c["usuario"] else "-")
            if c["tipo"] == "private":
                lineas.append(f"• Tu chat privado conmigo ({c['nombre']}): {c['id']}")
            else:
                lineas.append(f"• {c['nombre']}: {c['id']}")
            for tema, nombre in sorted(c.get("temas", {}).items()):
                log.info("      tema %s %s  ->  destino: %s/%s", tema, nombre, c["id"], tema)
                lineas.append(f"• {c['nombre']} › tema «{nombre or 'sin nombre'}»: {c['id']}/{tema}")
        # Para no tener que leer el registro de GitHub: se lo manda por Telegram a quien le escribió al bot.
        if lineas:
            aviso = ("¡Hola! Ya estoy funcionando. Estos son los lugares donde estoy y su código "
                     "(mandale una captura de esto a Claude):\n\n" + "\n".join(lineas))
            for c in chats:
                if c["tipo"] == "private":
                    try:
                        telegram_mensaje(self.token, str(c["id"]), aviso)
                    except ErrorPublicacion as error:
                        log.warning("no pude mandarle la lista a %s: %s", c["nombre"], error)
        return 0


def main() -> int:
    configurar_log()
    modo = (os.environ.get("MODO") or "prueba").strip().lower()
    if modo not in ("real", "prueba", "chats"):
        log.error("MODO desconocido: %s (usar real, prueba o chats)", modo)
        return 1
    try:
        cantidad = max(1, min(5, int(os.environ.get("CANTIDAD") or 1)))
    except ValueError:
        cantidad = 1
    forzar = (os.environ.get("FORZAR") or "").strip().lower() in ("1", "true", "si", "sí")
    corrida = Corrida(modo, cantidad, forzar, os.environ.get("PROMO") or "")
    local = corrida.ahora.astimezone(zona(corrida.cfg)).strftime("%d/%m %H:%M")
    log.info("Bot de noticias · modo %s · %s (hora de Argentina)", modo, local)
    try:
        return corrida.correr()
    finally:
        if corrida.real:
            # Pase lo que pase se guarda la memoria: así nunca se publica dos veces lo mismo.
            corrida.estado.guardar()


if __name__ == "__main__":
    sys.exit(main())
