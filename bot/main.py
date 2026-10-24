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
import io
import json
import os
import re
import sys
from datetime import date, datetime, timedelta

from PIL import Image

from . import calendario as cal
from . import grok
from . import ig_plan
from . import historias
from . import instagram
from . import ia
from .estado import Estado
from .filtro import Grupo, es_mala_noticia, nombra_prohibido, preparar, puntaje, ya_contada
from .fuentes import leer_fuentes
from .imagen import armar_placa, armar_promo, buscar_foto, generar_con_ia, logo_guardado, tal_cual
from .publicar import ErrorPublicacion, claves_x, telegram_album, telegram_bajar, telegram_chats, telegram_novedades, telegram_mensaje, telegram_publicar, telegram_puede_publicar, x_publicar, x_quien_soy
from .texto import (
    anio_actual, armar_llamado, enlaces_extra, faltantes, faltantes_promo, fuente_limpia, idea_de_imagen, normalizar_post, normalizar_promo,
    numeros_inventados, promesas, promo_telegram, promo_x, texto_revisable, texto_revisable_promo,
    LIMITE_X, cuentas_para, hashtags_de_charla, hashtags_para, unir_hashtags, peso_x, promesas_charla, recortar_x, sin_links, link_en_x, revisar_charla, sumar_hashtags, texto_propio_telegram, texto_propio_x, texto_telegram, texto_x, x_largo, x_premium,
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
        self.ocupado = False          # True si en esta corrida ya salió algo (no salen dos juntas, salvo lo urgente)
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
                if registro["x"] and registro.get("tipo") == "noticia":
                    self.avisar_etiquetar(registro["x"], para_x, cuentas_para(para_x, self.cfg.get("hashtags") or []))
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
        tags = hashtags_para({**post, "categoria": g.categoria}, self.cfg.get("hashtags") or [])
        para_telegram = sumar_hashtags(texto_telegram(post, g.urgente, llamado, self.redes()), tags)
        para_x = sumar_hashtags(texto_x(post, g.urgente, llamado, self.cfg["x"]), tags, LIMITE_X)
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
        if resultado == "ok" and self.real:
            self.mandar_cita(post, g)
            self.historia_de_noticia(g, post, foto, jpg)
        return resultado

    def avisar_etiquetar(self, id_post: str, texto: str, cuentas: list[str]) -> None:
        """Te avisa por privado a quién conviene etiquetar en un post que acaba de salir (X no deja hacerlo solo)."""
        if not cuentas or not (self.real and self.token and self.chat_prueba):
            return
        memoria = self.estado.datos["grok"]
        hoy = self.ahora.astimezone(zona(self.cfg)).date().isoformat()
        if memoria.get("etiquetar_dia") != hoy:
            memoria["etiquetar_dia"], memoria["etiquetar"] = hoy, 0
        if int(memoria.get("etiquetar", 0)) >= int(self.cfg.get("avisos_etiquetar_por_dia", 6) or 0):
            return
        memoria["etiquetar"] = int(memoria.get("etiquetar", 0)) + 1
        e = lambda s: html.escape(s, quote=False)
        primera = next((l for l in texto.splitlines() if l.strip()), "")[:120]
        mensaje = (f"🏷 <b>Para etiquetar</b>\n«{e(primera)}»\n\n"
                   f'<a href="https://x.com/i/status/{html.escape(str(id_post), quote=True)}">Abrí el post</a>, respondete a vos mismo o citalo con:\n'
                   f"<code>{e(' '.join(cuentas[:4]))} ¿qué opinan?</code>")
        try:
            telegram_mensaje(self.token, self.chat_prueba, mensaje, html=True)
        except ErrorPublicacion as error:
            log.warning("  no pude mandar el aviso para etiquetar: %s", error)

    def mandar_cita(self, post: dict, g: Grupo) -> None:
        """Noticia grande: Grok busca el post más fuerte sobre ella en X y te lo manda para que lo cites."""
        c = self.cfg.get("grok") or {}
        if not (verdadero(c.get("activa")) and grok.disponible() and self.token and self.chat_prueba):
            return
        if g.impacto < int(c.get("citar_impacto", 8)) or en_silencio(self.cfg, self.ahora):
            return
        memoria = self.estado.datos["grok"]
        hoy = self.ahora.astimezone(zona(self.cfg)).date().isoformat()
        if memoria.get("citas_dia") != hoy:
            memoria["citas_dia"], memoria["citas"] = hoy, 0
        if int(memoria.get("citas", 0)) >= int(c.get("citas_por_dia", 3)):
            return
        try:
            cita = grok.para_citar(self.cfg, self.ahora, post["titular"], post.get("resumen", ""))
        except grok.SinGrok as error:
            log.warning("  Grok no encontró qué citar: %s", error)
            return
        if not cita:
            return
        memoria["citas"] = int(memoria.get("citas", 0)) + 1
        e = lambda s: html.escape(s, quote=False)
        mensaje = (f"📣 <b>Noticia grande para citar</b>\n{e(post['titular'])}\n\n"
                   f'<a href="{html.escape(cita["url"], quote=True)}">Abrí el post de {e(cita["cuenta"] or "X")}</a>, tocá "Repostear" → "Citar" y poné tu mirada.\n\n'
                   f"💡 Idea: {e(cita['cita'])}")
        try:
            telegram_mensaje(self.token, self.chat_prueba, mensaje, html=True)
        except ErrorPublicacion as error:
            log.warning("  no pude mandar la noticia para citar: %s", error)

    def redes(self) -> dict | None:
        """El link a todas tus redes que va al pie de cada noticia ('redes' en 'marca')."""
        r = self.cfg["marca"].get("redes") or {}
        if isinstance(r, dict) and str(r.get("enlace") or "").startswith("http") and str(r.get("texto") or "").strip():
            return {"texto": str(r["texto"]).strip(), "enlace": str(r["enlace"]).strip()}
        return None

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
            # De a una por vez: si en esta corrida sale algo del calendario, una enseñanza o una pregunta,
            # la promo y las noticias comunes esperan a la próxima hora (lo urgente sale igual).
            if self.promo_pedida.lower() in ("encuesta", "pregunta", "lista", "dilema"):
                # Publicación a pedido (desde GitHub: modo real + promo = encuesta): sale ya, sin esperar su horario.
                salio = self.cerebro.disponible and self.publicar_charla(self.promo_pedida.lower())
                self.estado.guardar()
                return self.cerrar(1 if salio else 0)
            self.tal_vez_grok()              # no publica nada: busca temas del día y te manda los virales
            self.tal_vez_carrusel()          # Instagram: arma y publica el carrusel del día (y te avisa)
            self.tal_vez_numeros_ig()        # Instagram: lee cómo le fue a cada carrusel y los domingos te manda el ranking
            self.tal_vez_comentarios_ig()    # Instagram: a quien comenta la palabra le responde y le manda la guía por DM
            self.tal_vez_historias()         # Instagram: agenda, avisos de datos y promos en historias de @ezebagnato
            self.ocupado = self.tal_vez_material()      # lo que vos pedís por privado sale primero
            if not self.ocupado:
                self.ocupado = self.tal_vez_calendario()
            if not self.ocupado:
                self.ocupado = self.tal_vez_hilo()
            if not self.ocupado:
                self.ocupado = self.tal_vez_charla()
            if not self.ocupado:
                self.ocupado = self.tal_vez_idea()
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

        # --- muestra de charla para X (solo en modo prueba: promo = charla)
        if not self.real and self.promo_pedida.lower() == "hilo":
            return self.muestra_hilo()
        if not self.real and self.promo_pedida.lower() == "historias":
            return self.cerrar(1 if self.muestra_historias() else 0)
        if not self.real and self.promo_pedida.lower() == "instagram":
            return self.cerrar(1 if self.revisar_instagram() else 0)
        if not self.real and self.promo_pedida.lower() == "proximo":
            return self.cerrar(1 if self.hacer_carrusel(preparar=True) else 0)
        if not self.real and self.promo_pedida.lower().startswith("carrusel"):
            pedido = self.promo_pedida.lower().split(":", 1)
            hechas = sum(1 if self.hacer_carrusel(pedido[1].strip() if len(pedido) > 1 else "") else 0
                         for _ in range(max(1, self.cantidad)))
            return self.cerrar(hechas)
        if not self.real and self.promo_pedida.lower() == "estrategias":
            return self.cerrar(1 if grok.disponible() and self.mandar_estrategias() else 0)
        if not self.real and self.promo_pedida.lower() == "grok":
            return self.muestra_grok()
        if not self.real and self.promo_pedida.lower() == "calendario":
            return self.muestra_calendario()
        if not self.real and self.promo_pedida.lower() in ("charla", "ensenanza"):
            hechas = 0
            forzado = "ensenanza" if self.promo_pedida.lower() == "ensenanza" else ""
            for _ in range(max(1, self.cantidad)):
                hechas += 1 if self.publicar_charla(forzado) else 0
            return self.cerrar(hechas)

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
        promo = self.promo_que_toca() if self.real and hoy < tope and not self.ocupado else None

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
            # Lo urgente que salió de madrugada es un extra: no le saca lugar a las noticias del día.
            nocturnas = self.nocturnas_hoy()
            hoy -= nocturnas
            noticias_hoy = hoy - promos_hoy
            cupo = cupo_a_esta_hora(cfg, self.ahora)
            horarios_noticias = sorted(h for h in (a_hora(x) for x in (r.get("horarios_noticias") or [])) if h is not None)
            motivo = ""
            if en_silencio(cfg, self.ahora):
                motivo = "es horario de silencio"
            elif horarios_noticias:
                # Las noticias comunes tienen sus horarios, entre las enseñanzas y las promos: así en
                # Telegram sale algo más o menos cada hora y media, sin huecos largos ni amontonamientos.
                local = self.ahora.astimezone(zona(cfg)).time()
                pasados = sum(1 for h in horarios_noticias if h <= local)
                if noticias_hoy >= pasados:
                    motivo = f"van {noticias_hoy} noticias y todavía no es el horario de la siguiente"
                    cupo = hoy + 1           # para que no se active el 'día atrasado'
                else:
                    cupo = hoy + 1
            elif hoy >= cupo:
                motivo = f"ya van {hoy} publicaciones y a esta hora corresponden {cupo}"
            elif noticias_hoy >= tope - lugar_para_promos:
                motivo = "lo que queda del día está reservado para las promos"
            if not motivo and self.ocupado:
                motivo = "en esta corrida ya salió otra publicación (no salen dos juntas)"
            if motivo:
                umbral = urgente
                if self.ocupado:
                    # Ya salió otra cosa en esta corrida: solo lo gigante sale igual; lo demás, en la próxima hora.
                    umbral = max(urgente, int(r.get("impacto_urgente_ocupado", 10)))
                if en_silencio(cfg, self.ahora) and not verdadero(r.get("urgentes_de_noche", False)):
                    # De madrugada no sale nada: lo importante queda guardado y sale a la mañana.
                    umbral = 99
                log.info("Solo sale lo de impacto %d o más: %s.", umbral, motivo)
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

    # ------------------------------------------------- charla en X
    def nocturnas_hoy(self) -> int:
        """Cuántas noticias de hoy salieron en el horario de silencio (solo pueden ser urgentes)."""
        hoy = self.ahora.astimezone(zona(self.cfg)).date()
        total = 0
        for p in self.estado.datos["publicadas"]:
            try:
                fecha = datetime.fromisoformat(p["fecha"])
            except (KeyError, TypeError, ValueError):
                continue
            if fecha.astimezone(zona(self.cfg)).date() == hoy and p.get("tipo", "noticia") == "noticia" and en_silencio(self.cfg, fecha):
                total += 1
        return total

    def charla_toca(self, ensenanza: bool = False) -> bool:
        """¿Toca publicar una charla en X (o una enseñanza)? Una por cada horario ya pasado del día."""
        ch = self.cfg.get("ensenanza" if ensenanza else "x_charla") or {}
        if not verdadero(ch.get("activa")) or not self.canales or en_silencio(self.cfg, self.ahora):
            return False
        if not ensenanza and "x" not in self.canales:
            return False
        local = self.ahora.astimezone(zona(self.cfg))
        pasados = [h for h in (a_hora(h) for h in (ch.get("horarios") or [])) if h is not None and h <= local.time()]
        return self.estado.charlas_hoy(self.cfg, self.ahora, ensenanza) < len(pasados)

    def tal_vez_charla(self) -> bool:
        """Publica la enseñanza o la pregunta de X que toque (una sola por corrida). Devuelve si salió algo."""
        if not self.cerebro.disponible:
            return False
        salio = False
        for ensenanza in (True, False):
            if not self.charla_toca(ensenanza):
                continue
            forzado = "ensenanza" if ensenanza else ""
            if not ensenanza:
                # Una de las preguntas del día es una encuesta (votar es un toque: es lo que más interacción saca).
                turno = int((self.cfg.get("x_charla") or {}).get("encuesta_turno", 0) or 0)
                if turno and self.estado.charlas_hoy(self.cfg, self.ahora) == turno - 1:
                    forzado = "encuesta"
            try:
                salio = self.publicar_charla(forzado)
            except ia.SinCerebro as error:
                self.avisar("ia_caida_charla", f"La IA no respondió y no pude escribir la publicación. ({str(error)[:200]})")
            break                                        # la otra, si también tocaba, sale la próxima hora
        self.estado.guardar()
        return salio

    # ------------------------------------------------- calendario económico
    def publicar_suelto(self, tg_html: str, para_x: str, hashtags: list[str] | None = None) -> tuple[int | None, str]:
        """Publica algo que no es noticia ni promo (calendario) en Telegram (canal y grupo) y en X."""
        id_tg, id_x = None, ""
        tags = hashtags or []
        if self.redes():
            r = self.redes()
            tg_html += f'\n\n📲 <a href="{html.escape(r["enlace"], quote=True)}">{html.escape(r["texto"], quote=False)}</a>'
        tg_html = sumar_hashtags(tg_html, tags)
        if "telegram" in self.canales:
            for i, lugar in enumerate([self.destino, *self.tambien_en]):
                try:
                    resultado = telegram_publicar(self.token, lugar, None, tg_html)
                    id_tg = id_tg if i else resultado
                except ErrorPublicacion as error:
                    self.fallas += 1
                    self.avisar(error.clave, str(error))
        if "x" in self.canales:
            texto = cal.limpiar_x(para_x)
            texto = sumar_hashtags(texto, tags, None if x_premium(self.cfg["x"]) else LIMITE_X)
            if not x_premium(self.cfg["x"]):
                texto = recortar_x(texto)
            try:
                id_x = x_publicar(self.claves_x, None, texto)
            except ErrorPublicacion as error:
                self.fallas += 1
                self.avisar(error.clave, str(error))
        return id_tg, id_x

    # ------------------------------------------------- hilo semanal en X
    def tal_vez_hilo(self) -> bool:
        """Los domingos: 'lo que movió el mercado esta semana', como hilo en X y un resumen en Telegram."""
        c = self.cfg.get("hilo_semanal") or {}
        if not verdadero(c.get("activa")) or not self.canales or en_silencio(self.cfg, self.ahora):
            return False
        local = self.ahora.astimezone(zona(self.cfg))
        hora = a_hora(c.get("hora") or "11:00")
        if local.weekday() != int(c.get("dia", 6)) or hora is None or local.time() < hora:
            return False
        memoria = self.estado.datos["hilo"]
        if memoria.get("ultimo") == local.date().isoformat():
            return False
        memoria["ultimo"] = local.date().isoformat()
        self.estado.guardar()
        return self.publicar_hilo()

    def publicar_hilo(self) -> bool:
        c = self.cfg.get("hilo_semanal") or {}
        desde = self.ahora - timedelta(days=7)
        noticias = []
        for p in self.estado.datos["publicadas"]:
            try:
                if p.get("tipo", "noticia") == "noticia" and datetime.fromisoformat(p["fecha"]) >= desde:
                    noticias.append({"titular": p.get("titular", ""), "categoria": p.get("categoria", ""), "impacto": p.get("impacto", 0)})
            except (KeyError, TypeError, ValueError):
                continue
        cantidad = int(c.get("cantidad", 5))
        if len(noticias) < cantidad:
            log.info("Hilo semanal: hay pocas noticias en la semana (%d); no lo armo.", len(noticias))
            return False
        noticias.sort(key=lambda n: -int(n.get("impacto") or 0))
        # La numeración del hilo ("1/", "en 5 posts") no son cifras inventadas.
        referencia = " ".join(n["titular"] for n in noticias) + " " + " ".join(str(i) for i in range(1, cantidad + 3))
        hilo, correccion = None, ""
        for _ in range(2):
            crudo = ia.redactar_hilo(self.cerebro, noticias[:25], cantidad, correccion)
            partes = [str(crudo.get("gancho") or "").strip()] + [str(x).strip() for x in (crudo.get("posts") or []) if str(x).strip()] + [str(crudo.get("cierre") or "").strip()]
            partes = [sin_links(x) for x in partes if x]
            inventados = numeros_inventados(" ".join(partes), referencia, anio_actual(self.ahora))
            plata = any(re.search(r"(?i)\bplata\b", x) for x in partes)
            if len(partes) >= cantidad + 1 and not inventados and not plata and all(peso_x(x) <= 280 for x in partes):
                hilo = partes
                break
            correccion = "tu intento anterior no sirve: " + ("; ".join(filter(None, [
                f"pusiste cifras que no están en los títulos ({', '.join(inventados)})" if inventados else "",
                'dijiste "plata" (tiene que ser "dinero")' if plata else "",
                "algún post pasa los 260 caracteres" if any(peso_x(x) > 280 for x in partes) else "",
                f"faltan posts (tienen que ser {cantidad})" if len(partes) < cantidad + 1 else "",
            ]))) + "."
        if hilo is not None:
            # Los hashtags de los temas de la semana van en el primer post (el que más se ve).
            tags = hashtags_de_charla(crudo) or hashtags_para({"titular": " ".join(hilo), "resumen": "", "categoria": ""}, self.cfg.get("hashtags") or [])
            hilo[0] = sumar_hashtags(hilo[0], tags, LIMITE_X)
        if hilo is None:
            self.avisar("hilo", "No pude armar el hilo semanal: la IA no lo escribió bien dos veces. Sale el domingo que viene.")
            return False
        if not self.real:
            if self.token and self.chat_prueba:
                telegram_mensaje(self.token, self.chat_prueba, "🧵 Así saldría el hilo semanal en X:\n\n" + "\n\n— — —\n\n".join(hilo))
            return True
        anterior = ""
        for i, parte in enumerate(hilo):
            try:
                anterior = x_publicar(self.claves_x, None, parte, en_respuesta_a=anterior) if "x" in self.canales else ""
            except ErrorPublicacion as error:
                self.fallas += 1
                self.avisar(error.clave, f"El hilo semanal se cortó en el post {i + 1}: {error}")
                break
            if not anterior:
                break
        if "telegram" in self.canales:
            cuerpo = "🗓 <b>" + html.escape(hilo[0].split("\n")[0], quote=False) + "</b>\n\n" + "\n\n".join(html.escape(x, quote=False) for x in hilo[1:])
            for i, lugar in enumerate([self.destino, *self.tambien_en]):
                try:
                    telegram_publicar(self.token, lugar, None, cuerpo)
                except ErrorPublicacion as error:
                    self.fallas += 1
                    self.avisar(error.clave, str(error))
        log.info("Hilo semanal publicado (%d posts)", len(hilo))
        return True

    def muestra_hilo(self) -> int:
        return self.cerrar(1 if self.publicar_hilo() else 0)

    # ------------------------------------------------- Grok (X en tiempo real)
    def tema_caliente(self) -> dict | None:
        """Un tema de los que más se hablan hoy en X que todavía no se usó."""
        g = self.estado.datos["grok"]
        hoy = self.ahora.astimezone(zona(self.cfg)).date().isoformat()
        if g.get("temas_dia") != hoy:
            return None
        usados = set(g.get("usados") or [])
        for t in g.get("temas") or []:
            if isinstance(t, dict) and t.get("tema") and t["tema"] not in usados:
                if self.real:
                    g["usados"] = [*usados, t["tema"]]
                return t
        return None

    def tal_vez_grok(self) -> None:
        c = self.cfg.get("grok") or {}
        if not verdadero(c.get("activa")) or not grok.disponible() or en_silencio(self.cfg, self.ahora):
            return
        g = self.estado.datos["grok"]
        local = self.ahora.astimezone(zona(self.cfg))
        hoy = local.date().isoformat()
        # Temas del día: se buscan a la mañana y se refrescan a la tarde.
        horarios = [h for h in (a_hora(x) for x in (c.get("temas_horas") or ["08:00", "15:00"])) if h is not None]
        tandas = sum(1 for h in horarios if h <= local.time())
        hechas = int(g.get("temas_tandas", 0)) if g.get("temas_dia") == hoy else 0
        if tandas > hechas:
            try:
                temas, ganchos = grok.temas_del_dia(self.cfg, self.ahora)
                usados = g.get("usados", []) if g.get("temas_dia") == hoy else []
                g.update({"temas": temas, "temas_dia": hoy, "temas_tandas": tandas, "usados": usados,
                          "ganchos": ganchos or g.get("ganchos", [])})
                log.info("Grok: temas de hoy en X: %s", " | ".join(t["tema"] for t in temas))
            except grok.SinGrok as error:
                g.update({"temas_dia": hoy, "temas_tandas": tandas})      # no se insiste hasta la próxima tanda
                self.avisar("grok", f"Grok {error}. Las preguntas de X siguen saliendo con temas generales.", cada_horas=24)
        # Informe semanal de estrategias (qué hacen las cuentas que más crecen), los lunes.
        hora_est = a_hora(c.get("estrategias_hora") or "09:00")
        if (local.weekday() == int(c.get("estrategias_dia", 0)) and g.get("estrategias_dia") != hoy
                and hora_est is not None and local.time() >= hora_est):
            g["estrategias_dia"] = hoy
            self.mandar_estrategias()
        # Posts virales para responder: una vez por día, a tu privado.
        hora_virales = a_hora(c.get("virales_hora") or "10:00")
        if g.get("virales_dia") != hoy and hora_virales is not None and local.time() >= hora_virales:
            g["virales_dia"] = hoy
            self.mandar_virales()
        self.estado.guardar()

    def mandar_virales(self) -> int:
        c = self.cfg.get("grok") or {}
        try:
            posts = grok.virales(self.cfg, self.ahora, int(c.get("virales_cantidad", 5)))
        except grok.SinGrok as error:
            self.avisar("grok", f"Grok {error}. Hoy no pude buscarte los posts virales.", cada_horas=24)
            return 0
        if not posts or not (self.token and self.chat_prueba):
            return 0
        e = lambda s: html.escape(s, quote=False)
        partes = ["🔥 <b>Posts virales para responder hoy</b>",
                  "Respondé desde tu celu con algo que aporte: un dato, tu experiencia o una pregunta. Lo ideal, en la primera hora.", ""]
        for i, p in enumerate(posts, start=1):
            partes.append(f'<b>{i}. <a href="{html.escape(p["url"], quote=True)}">{e(p["cuenta"] or "ver post")}</a></b>')
            if p["de_que_habla"]:
                partes.append(e(p["de_que_habla"]))
            if p["por_que"]:
                partes.append(f"<i>{e(p['por_que'])}</i>")
            if p["idea"]:
                partes.append(f"💡 Idea: {e(p['idea'])}")
            partes.append("")
        try:
            telegram_mensaje(self.token, self.chat_prueba, "\n".join(partes).strip(), html=True)
        except ErrorPublicacion as error:
            log.warning("no pude mandar los virales: %s", error)
            return 0
        self.mandar_inspiracion()
        return len(posts)

    def mandar_estrategias(self) -> int:
        try:
            datos = grok.estrategias(self.cfg, self.ahora)
        except grok.SinGrok as error:
            log.warning("Grok no pudo armar el informe de estrategias: %s", error)
            return 0
        lista = [x for x in (datos.get("estrategias") or []) if isinstance(x, dict) and x.get("nombre")][:6]
        if not lista or not (self.token and self.chat_prueba):
            return 0
        e = lambda s: html.escape(str(s or ""), quote=False)
        partes = ["📊 <b>Estrategias de la semana: qué hacen en X las cuentas que más crecen</b>",
                  "Las que puedo hacer solo, las hago. Las que necesitan algo tuyo, te lo pido abajo de cada una.", ""]
        for i, x in enumerate(lista, start=1):
            partes.append(f"<b>{i}. {e(x.get('nombre'))}</b>" + (f" ({e(x.get('quien'))})" if x.get("quien") else ""))
            partes.append(e(x.get("que_hacen")))
            partes.append(f"👉 <i>Cómo lo aplicamos:</i> {e(x.get('como_aplicarlo'))}")
            if x.get("material") and x.get("pedido"):
                partes.append(f"📸 <b>Necesito de vos:</b> {e(x.get('material'))}")
                partes.append(f"Mandámelo acá con este texto: <code>{e(x.get('pedido'))}</code>")
            url = str(x.get("ejemplo_url") or "")
            if url.startswith("https://x.com/") or url.startswith("https://twitter.com/"):
                partes.append(f'<a href="{html.escape(url, quote=True)}">Ver ejemplo</a>')
            partes.append("")
        try:
            telegram_mensaje(self.token, self.chat_prueba, "\n".join(partes).strip(), html=True)
        except ErrorPublicacion as error:
            log.warning("no pude mandar el informe: %s", error)
            return 0
        return len(lista)

    def mandar_inspiracion(self) -> None:
        """Busca posts que pegaron por su formato y guarda los borradores propios: los publica el bot en sus horarios."""
        c = self.cfg.get("grok") or {}
        cantidad = int(c.get("inspiracion_cantidad", 2) or 0)
        if cantidad <= 0:
            return
        try:
            ideas = grok.inspiracion(self.cfg, self.ahora, cantidad)
        except grok.SinGrok as error:
            log.warning("Grok no encontró inspiración: %s", error)
            return
        buenas = [i for i in ideas if not re.search(r"(?i)\bplata\b", i["borrador"]) and not promesas_charla(i["borrador"])]
        g = self.estado.datos["grok"]
        g["ideas"] = buenas
        g["ideas_dia"] = self.ahora.astimezone(zona(self.cfg)).date().isoformat()
        log.info("Grok: %d ideas de posts para hoy", len(buenas))
        if not self.real and self.token and self.chat_prueba:
            for idea in buenas:
                telegram_mensaje(self.token, self.chat_prueba, f"✍️ Idea que publicaría hoy (inspirada en {idea['url']}):\n\n{idea['borrador']}")

    def tal_vez_idea(self) -> bool:
        """Publica en X la próxima idea del día (inspirada en un post que pegó por su formato)."""
        c = self.cfg.get("grok") or {}
        if "x" not in self.canales or en_silencio(self.cfg, self.ahora):
            return False
        g = self.estado.datos["grok"]
        hoy = self.ahora.astimezone(zona(self.cfg)).date().isoformat()
        local = self.ahora.astimezone(zona(self.cfg)).time()
        horarios = [h for h in (a_hora(x) for x in (c.get("ideas_horarios") or [])) if h is not None]
        if g.get("ideas_dia") != hoy or self.estado.formato_hoy(self.cfg, self.ahora, "idea") >= sum(1 for h in horarios if h <= local):
            return False
        pendientes = [i for i in g.get("ideas") or [] if not i.get("usada")]
        anteriores = self.estado.charlas_recientes()
        premium = x_premium(self.cfg["x"])
        for idea in pendientes:
            idea["usada"] = True
            texto, problemas = revisar_charla({"texto": idea["borrador"]}, anteriores, 1000 if premium else 280)
            if problemas:
                log.info("  idea descartada: %s", problemas)
                continue
            tags = hashtags_para({"titular": texto, "resumen": "", "categoria": ""}, self.cfg.get("hashtags") or [])
            texto = sumar_hashtags(texto, tags, None if premium else LIMITE_X)
            try:
                id_post = x_publicar(self.claves_x, None, texto)
            except ErrorPublicacion as error:
                self.fallas += 1
                self.avisar(error.clave, str(error))
                return False
            self.estado.agregar_charla({"fecha": self.ahora.isoformat(), "formato": "idea", "tema": idea.get("url", ""), "texto": texto, "x": id_post})
            opciones = (self.cfg.get("x_charla") or {}).get("respuestas") or []
            if id_post and opciones:
                try:
                    x_publicar(self.claves_x, None, str(opciones[len(self.estado.datos["charlas"]) % len(opciones)]), en_respuesta_a=id_post)
                except ErrorPublicacion as error:
                    log.warning("  no pude dejar la respuesta: %s", error)
            if id_post:
                self.avisar_etiquetar(id_post, texto, cuentas_para(texto, self.cfg.get("hashtags") or []))
                # Te avisa qué publicó y en qué post viral se inspiró (para que veas el formato).
                try:
                    origen = f'\n\n🔎 Inspirada en <a href="{html.escape(idea.get("url", ""))}">este post de {html.escape(idea.get("cuenta", ""))}</a>' if idea.get("url") else ""
                    por_que = f'\n<i>Por qué pegó: {html.escape(idea.get("por_que_funciono", ""))}</i>' if idea.get("por_que_funciono") else ""
                    telegram_mensaje(self.token, self.chat_prueba,
                                     f'💡 <b>Publiqué la idea viral del día en X</b>\n\n{html.escape(texto)}\n\n👉 https://x.com/i/status/{id_post}{origen}{por_que}',
                                     html=True)
                except ErrorPublicacion:
                    pass
            log.info("Idea publicada en X: %s", texto.replace("\n", " / ")[:100])
            self.estado.guardar()
            return True
        self.estado.guardar()
        return False

    # ------------------------------------------------- material que manda Eze al privado
    def tal_vez_material(self) -> bool:
        """Lee lo que Eze le mandó al robot por privado. 'post: …' o 'hilo: …' (con o sin captura) = el bot lo
        escribe con sus datos y lo publica en X. Le responde con el link."""
        if not (self.token and self.chat_prueba):
            return False
        memoria = self.estado.datos["material"]
        try:
            novedades = telegram_novedades(self.token, int(memoria.get("offset", 0) or 0))
        except ErrorPublicacion as error:
            log.warning("no pude leer el privado: %s", error)
            return False
        pedidos: dict[str, dict] = {}
        for n in novedades:
            memoria["offset"] = int(n.get("update_id", 0)) + 1
            m = n.get("message") or {}
            if str((m.get("chat") or {}).get("id")) != str(self.chat_prueba):
                continue                                     # solo Eze puede pedir publicaciones
            clave = str(m.get("media_group_id") or m.get("message_id"))
            p = pedidos.setdefault(clave, {"texto": "", "fotos": []})
            texto = str(m.get("caption") or m.get("text") or "").strip()
            if texto:
                p["texto"] = texto
            if m.get("photo"):
                p["fotos"].append(max(m["photo"], key=lambda f: int(f.get("file_size") or 0))["file_id"])
            elif str((m.get("document") or {}).get("mime_type", "")).startswith("image/"):
                p["fotos"].append(m["document"]["file_id"])          # foto mandada "como archivo"
        publicado = False
        es_ig = lambda t: bool(re.match(r"(?i)^\s*(ig|fotos?|fotos? ig|para ig)\b", t or ""))
        # Si "ig" vino en un mensaje aparte (antes o después de las fotos), se lleva las fotos sin texto.
        # Fotos sin texto (o con "ig") = para los carruseles de Instagram.
        sueltas = [f for p in pedidos.values() if p["fotos"] and (es_ig(p["texto"]) or not p["texto"]) for f in p["fotos"]]
        if sueltas:
            self.guardar_fotos_ig(sueltas)
        for p in pedidos.values():
            if es_ig(p["texto"]) or (p["fotos"] and not p["texto"]):
                continue
            match = re.match(r"(?is)^\s*(post|hilo)\s*:\s*(.+)$", p["texto"])
            if not match or "x" not in self.canales:
                continue
            publicado = self.publicar_material(match.group(1).lower(), match.group(2), p["fotos"]) or publicado
        self.estado.guardar()
        return publicado

    # ------------------------------------------------- Instagram: carruseles
    def guardar_fotos_ig(self, fotos: list[str]) -> None:
        """Fotos que Eze manda al privado con 'ig' o 'fotos': se usan en los carruseles de pantalla partida."""
        g = self.estado.datos["ig"]
        banco = [f for f in (g.get("fotos") or []) if f not in fotos] + fotos
        g["fotos"] = banco[-40:]
        try:
            telegram_mensaje(self.token, self.chat_prueba, f"✅ Guardé {len(fotos)} foto(s) para los carruseles de Instagram. Tengo {len(g['fotos'])} en total.")
        except ErrorPublicacion:
            pass

    def tal_vez_carrusel(self) -> bool:
        c = self.cfg.get("instagram") or {}
        if not verdadero(c.get("carruseles")) or not (self.token and self.chat_prueba):
            return False
        local = self.ahora.astimezone(zona(self.cfg))
        hora = a_hora(c.get("hora_carrusel") or "18:00")
        dias = [int(d) for d in (c.get("dias_carrusel") or [0, 2, 4, 6])]
        if local.weekday() not in dias or hora is None or local.time() < hora:
            return False
        g = self.estado.datos["ig"]
        if g.get("ultimo") == local.date().isoformat():
            return False
        g["ultimo"] = local.date().isoformat()
        self.estado.guardar()
        return self.hacer_carrusel()

    def hacer_carrusel(self, estilo_pedido: str = "", preparar: bool = False) -> bool:
        """Arma el carrusel que toca en la rueda (todos los estilos y tipos, rotando). Mientras Instagram no esté
        conectado, se lo manda a Eze por privado listo para subir; después se va a publicar solo."""
        g = self.estado.datos["ig"]
        tipo, estilo, tema = ig_plan.que_toca(g)
        if estilo_pedido:
            par = next(((t, e) for t, e in ig_plan.RUEDA if estilo_pedido in (t, e)), None)
            if par:
                tipo, estilo = par
                tema = ig_plan.que_toca({"vuelta": ig_plan.RUEDA.index(par), "temas_usados": g.get("temas_usados")})[2]
        # Si ya se dejó armado (y revisado por Eze) el que toca, sale ese mismo, sin reescribirlo.
        proximo = g.get("proximo") or {}
        listas = None
        if not estilo_pedido and not preparar and proximo.get("archivos"):
            # Láminas diseñadas a mano y guardadas en el repo: salen tal cual.
            try:
                listas = [(RAIZ / a).read_bytes() for a in proximo["archivos"]]
                tipo, estilo, tema = proximo.get("tipo", tipo), proximo.get("estilo", estilo), proximo.get("tema", tema)
                contenido = {"texto_post": proximo["texto_post"], "palabra": proximo.get("palabra", "")}
            except (OSError, KeyError) as error:
                log.warning("no pude leer el carrusel armado: %s", error)
                listas = None
        if listas is not None:
            pass
        elif not estilo_pedido and not preparar and proximo.get("tema") == tema and proximo.get("estilo") == estilo:
            contenido = proximo["contenido"]
        else:
            contenido = ig_plan.redactar(self.cerebro, tipo, estilo, tema)
        if not contenido:
            self.avisar("carrusel", f"No pude armar el carrusel de Instagram ({tema}): la IA no lo escribió bien.")
            return False
        fotos = []
        if estilo == "partida" and listas is None:
            for f in (g.get("fotos") or [])[-8:]:
                b = telegram_bajar(self.token, f)
                if b:
                    try:
                        fotos.append(Image.open(io.BytesIO(b)).convert("RGB"))
                    except OSError:
                        continue
        laminas = listas if listas is not None else ig_plan.armar(contenido, estilo, fotos, tema=tema)
        aviso = ""
        if estilo == "partida" and not fotos and listas is None:
            aviso = "\n\n📸 <b>Necesito fotos tuyas</b> para este estilo (operando, en tu escritorio, de viaje). Mandámelas acá con el texto <code>ig</code> y las uso en los próximos."
        if preparar:
            g["proximo"] = {"tipo": tipo, "estilo": estilo, "tema": tema, "contenido": contenido}
            self.estado.guardar()
            try:
                telegram_album(self.token, self.chat_prueba, laminas,
                               f"👀 <b>Así va a salir el próximo carrusel</b> · {html.escape(tema)}\n<i>Estilo {estilo} · {tipo}</i>\n\nSi ves un error, decíselo a Claude antes de que salga.")
                telegram_mensaje(self.token, self.chat_prueba, "📝 Texto del post:\n\n<code>" + html.escape(contenido["texto_post"]) + "</code>", html=True)
            except ErrorPublicacion as error:
                log.warning("no pude mandar la vista previa: %s", error)
                return False
            return True
        media_id = ""
        ig_cfg = self.cfg.get("instagram") or {}
        if self.real and verdadero(ig_cfg.get("publicar", True)) and instagram.llave("ezebagnato", g):
            media_id = self.subir_a_instagram("ezebagnato", laminas, contenido["texto_post"])
        if media_id and self.historias_activas():
            try:
                self.subir_historia("ezebagnato", historias.html_carrusel(laminas[0], tema, "@ezebagnato"), "carrusel")
            except Exception as error:            # la historia es un extra: nunca frena el carrusel
                log.warning("historia del carrusel: %s", error)
        if media_id:
            cabeza = (f"✅ <b>Publiqué el carrusel en @ezebagnato</b> · {html.escape(tema)}\n<i>Estilo {estilo} · {tipo}</i>\n\n"
                      "Respondé los comentarios en la primera hora: es lo que más lo empuja.")
        else:
            cabeza = (f"🖼 <b>Carrusel para Instagram</b> · {html.escape(tema)}\n<i>Estilo {estilo} · {tipo}</i>\n\n"
                      + ("Es una muestra: no se publicó." if not self.real else "No lo pude publicar solo: te lo dejo para que lo subas vos."))
        try:
            telegram_album(self.token, self.chat_prueba, laminas, cabeza)
            telegram_mensaje(self.token, self.chat_prueba, "📝 Texto para el post (copialo):\n\n<code>"
                             + html.escape(contenido["texto_post"]) + "</code>" + aviso, html=True)
        except ErrorPublicacion as error:
            log.warning("no pude mandar el carrusel: %s", error)
            return False
        if self.real:
            g.pop("proximo", None)
            g["vuelta"] = int(g.get("vuelta", 0) or 0) + 1
            g["temas_usados"] = ([*(g.get("temas_usados") or []), tema])[-80:]
            g["historial"] = ([*(g.get("historial") or []), {"fecha": self.ahora.isoformat(), "tipo": tipo, "estilo": estilo,
                                                              "tema": tema, "palabra": contenido["palabra"], "media_id": media_id}])[-200:]
            self.estado.guardar()
        return True

    def subir_a_instagram(self, cuenta: str, laminas: list[bytes], texto: str) -> str:
        g = self.estado.datos["ig"]
        carpeta = f"{self.ahora:%Y%m%d-%H%M}-{cuenta}"
        try:
            urls = instagram.subir_imagenes(laminas, carpeta)
            try:
                return instagram.publicar_carrusel(instagram.llave(cuenta, g), urls, texto)
            finally:
                instagram.borrar_imagenes(carpeta, len(laminas))
        except instagram.ErrorInstagram as error:
            self.avisar("instagram", f"No pude publicar en Instagram ({cuenta}): {error}")
            return ""

    def revisar_instagram(self) -> bool:
        """Prueba las llaves de Instagram y te cuenta qué cuentas quedaron conectadas."""
        g = self.estado.datos["ig"]
        renglones = []
        for cuenta in instagram.CUENTAS:
            t = instagram.llave(cuenta, g)
            if not t:
                renglones.append(f"❌ {cuenta}: falta la llave {instagram.CUENTAS[cuenta]} en GitHub")
                continue
            try:
                yo = instagram.quien_soy(t)
                renglones.append(f"✅ @{yo.get('username')}: conectada ({yo.get('followers_count', '?')} seguidores, {yo.get('media_count', '?')} publicaciones)")
            except instagram.ErrorInstagram as error:
                renglones.append(f"❌ {cuenta}: {error}")
        try:
            telegram_mensaje(self.token, self.chat_prueba, "📸 Instagram\n\n" + "\n".join(renglones))
        except ErrorPublicacion:
            pass
        log.warning("Instagram: %s", " | ".join(renglones))   # sale como anotación en GitHub
        return all(r.startswith("✅") for r in renglones)

    def tal_vez_numeros_ig(self) -> None:
        """Una vez por día: renueva las llaves si toca y lee cómo le fue a cada carrusel (a los 3 y a los 7 días).
        Los domingos a la noche te manda qué estilo y qué tipo de contenido rinden más."""
        g = self.estado.datos["ig"]
        if not instagram.disponibles(g):
            return
        local = self.ahora.astimezone(zona(self.cfg))
        hoy = local.date().isoformat()
        if g.get("numeros_dia") != hoy:
            g["numeros_dia"] = hoy
            for cuenta in instagram.disponibles(g):
                instagram.renovar(cuenta, g)
            t = instagram.llave("ezebagnato", g)
            for h in g.get("historial") or []:
                if not (t and h.get("media_id")):
                    continue
                try:
                    dias = (self.ahora - datetime.fromisoformat(h["fecha"])).days
                except (KeyError, ValueError):
                    continue
                if 3 <= dias <= 10 and (h.get("numeros_a") or 0) < (7 if dias >= 7 else 3):
                    try:
                        h["numeros"] = instagram.numeros(t, h["media_id"])
                        h["numeros_a"] = 7 if dias >= 7 else 3
                    except instagram.ErrorInstagram as error:
                        log.warning("Instagram: no pude leer los números de %s: %s", h.get("tema"), error)
            self.estado.guardar()
        if local.weekday() == 6 and local.hour >= 20 and g.get("ranking_dia") != hoy:
            g["ranking_dia"] = hoy
            self.estado.guardar()
            self.mandar_ranking_ig()

    # ------------------------------------------------- Instagram: historias
    def historias_activas(self) -> bool:
        h = (self.cfg.get("instagram") or {}).get("historias") or {}
        return self.real and verdadero(h.get("activa")) and historias.disponible()

    def subir_historia(self, cuenta: str, pagina_html: str, tipo: str) -> bool:
        """Saca la foto de la historia, la publica y la anota. Respeta el máximo por día de cada cuenta."""
        cfg_h = (self.cfg.get("instagram") or {}).get("historias") or {}
        g = self.estado.datos["ig"]
        mem = g.setdefault("historias", {})
        hoy = self.ahora.astimezone(zona(self.cfg)).date().isoformat()
        if mem.get("dia") != hoy:
            mem["dia"], mem["conteo"] = hoy, {}
        tope = int(cfg_h.get("max_ezebagnato" if cuenta == "ezebagnato" else "max_comunidad", 3 if cuenta == "ezebagnato" else 6))
        if int(mem["conteo"].get(cuenta, 0)) >= tope:
            log.info("Historias de %s: ya van %d hoy (tope).", cuenta, tope)
            return False
        token = instagram.llave(cuenta, g)
        if not token:
            return False
        jpg = historias.sacar_foto(pagina_html)
        carpeta = f"{self.ahora:%Y%m%d-%H%M%S}-{cuenta}-historia"
        try:
            urls = instagram.subir_imagenes([jpg], carpeta)
            try:
                instagram.publicar_historia(token, urls[0])
            finally:
                instagram.borrar_imagenes(carpeta, 1)
        except instagram.ErrorInstagram as error:
            self.avisar("ig_historia", f"No pude subir una historia a Instagram ({cuenta}): {error}")
            return False
        mem["conteo"][cuenta] = int(mem["conteo"].get(cuenta, 0)) + 1
        mem.setdefault("hechas", []).append({"fecha": self.ahora.isoformat(), "cuenta": cuenta, "tipo": tipo})
        mem["hechas"] = mem["hechas"][-100:]
        self.estado.guardar()
        log.info("Historia de %s publicada en %s.", tipo, cuenta)
        return True

    def tal_vez_historias(self) -> None:
        """@ezebagnato: la agenda económica a la mañana, el aviso antes de un dato fuerte y una promo cada 2 días."""
        if not self.historias_activas() or en_silencio(self.cfg, self.ahora):
            return
        cfg_h = (self.cfg.get("instagram") or {}).get("historias") or {}
        mem = self.estado.datos["ig"].setdefault("historias", {})
        local = self.ahora.astimezone(zona(self.cfg))
        hoy = local.date().isoformat()
        try:
            eventos = cal.leer(self.cfg, self.ahora) if verdadero((self.cfg.get("calendario") or {}).get("activa")) else []
            # 1) Agenda del día
            hora_agenda = a_hora(cfg_h.get("agenda_hora") or "07:45")
            if mem.get("agenda") != hoy and hora_agenda and local.time() >= hora_agenda:
                mem["agenda"] = hoy
                de_hoy = [ev for ev in cal.de_hoy(eventos, self.cfg, self.ahora) if ev["fecha"] > self.ahora]
                if de_hoy:
                    filas = [{"hora": f"{ev['fecha']:%H:%M}", "pais": ev["pais"], "nombre": ev["nombre"],
                              "prevision": ev["prevision"], "anterior": ev["anterior"]} for ev in de_hoy]
                    meses = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
                    dias = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
                    fecha = f"{dias[local.weekday()]} {local.day} de {meses[local.month - 1]}"
                    self.subir_historia("ezebagnato", historias.html_agenda(filas, fecha, "@ezebagnato"), "agenda")
                    return
            # 2) Aviso antes de un dato fuerte
            avisados = mem.setdefault("avisos", [])
            proximos = [ev for ev in cal.proximos(eventos, self.ahora, 20, 75) if ev["clave"] not in avisados]
            if proximos:
                ev = proximos[0]
                avisados.extend(x["clave"] for x in proximos)
                mem["avisos"] = avisados[-50:]
                minutos = round((ev["fecha"] - self.ahora).total_seconds() / 60 / 5) * 5
                cuando = "EN 1 HORA" if minutos >= 55 else f"EN {minutos} MIN"
                self.subir_historia("ezebagnato", historias.html_aviso(cuando, f"{ev['fecha']:%H:%M}", ev["pais"], ev["nombre"],
                                                                       ev["prevision"], ev["anterior"], "@ezebagnato"), "aviso")
                return
        except Exception as error:                 # las historias son un extra: nunca frenan al resto
            log.warning("historias (calendario): %s", error)
        # 3) Promo cada N días
        try:
            hora_promo = a_hora(cfg_h.get("promo_hora") or "20:00")
            ultima = mem.get("promo_dia")
            cada = int(cfg_h.get("promo_cada_dias", 2))
            pasaron = (local.date() - date.fromisoformat(ultima)).days if ultima else 99
            if hora_promo and local.time() >= hora_promo and pasaron >= cada:
                candidatas = [p for p in self.cfg.get("promos") or [] if verdadero(p.get("activa", True)) and p.get("imagen")
                              and verdadero(p.get("en_historias", True)) and (RAIZ / str(p["imagen"])).exists()
                              and not self.promo_vencida(p)]
                if candidatas:
                    mem["promo_dia"] = hoy
                    p = candidatas[int(mem.get("promo_vuelta", 0)) % len(candidatas)]
                    mem["promo_vuelta"] = int(mem.get("promo_vuelta", 0)) + 1
                    titulo = str(p.get("titulo_historia") or p.get("nombre") or "")
                    self.subir_historia("ezebagnato", historias.html_promo((RAIZ / str(p["imagen"])).read_bytes(), titulo, "@ezebagnato"), "promo")
        except Exception as error:
            log.warning("historias (promo): %s", error)

    def muestra_historias(self) -> bool:
        """Prueba: arma las historias con datos de hoy y te las manda por Telegram (no publica nada)."""
        fotos = []
        try:
            eventos = cal.de_hoy(cal.leer(self.cfg, self.ahora), self.cfg, self.ahora)
            filas = [{"hora": f"{ev['fecha']:%H:%M}", "pais": ev["pais"], "nombre": ev["nombre"], "prevision": ev["prevision"],
                      "anterior": ev["anterior"]} for ev in eventos] or [{"hora": "--:--", "pais": "USD", "nombre": "Hoy no hay datos fuertes", "prevision": "", "anterior": ""}]
            fotos.append(historias.sacar_foto(historias.html_agenda(filas, "muestra de hoy", "@ezebagnato")))
            promo = next((p for p in self.cfg.get("promos") or [] if verdadero(p.get("activa", True)) and p.get("imagen")
                          and verdadero(p.get("en_historias", True)) and (RAIZ / str(p["imagen"])).exists()), None)
            if promo:
                fotos.append(historias.sacar_foto(historias.html_promo((RAIZ / str(promo["imagen"])).read_bytes(),
                                                                       str(promo.get("titulo_historia") or promo.get("nombre")), "@ezebagnato")))
            # Una noticia de ejemplo con foto real (la Fed), para ver el diseño.
            from .imagen import buscar_foto
            foto = buscar_foto(["Federal Reserve Eccles Building", "Federal Reserve Board"], "La Fed bajó la tasa", lambda t: False)
            datos, es_logo, credito = None, False, ""
            if foto is not None and foto.imagen is not None:
                es_logo = bool(foto.es_logo or foto.origen == "marca")
                b = io.BytesIO()
                (foto.imagen.convert("RGBA").save(b, "PNG") if es_logo else foto.imagen.convert("RGB").save(b, "JPEG", quality=90))
                datos, credito = b.getvalue(), ("" if es_logo else foto.credito())
            fotos.append(historias.sacar_foto(historias.html_noticia(
                "EJEMPLO: La Fed bajó la tasa 25 puntos básicos", "La Reserva Federal recortó su tasa de referencia por tercera vez en el año.",
                datos, es_logo, credito, "Economía", True, "Reserva Federal", "@comunidad_inversores_ok")))
        except Exception as error:
            self.avisar("historias_prueba", f"No pude armar las historias de prueba: {type(error).__name__}: {error}")
            self.fallas += 1
            return False
        try:
            telegram_album(self.token, self.chat_prueba, fotos, "📱 <b>Muestra de historias</b> (no se publicó nada)")
        except ErrorPublicacion as error:
            log.warning("%s", error)
            return False
        log.warning("Historias de prueba armadas: %d", len(fotos))
        return True

    def promo_vencida(self, p: dict) -> bool:
        vence = str(p.get("vence") or "").strip()
        if not vence:
            return False
        try:
            return self.ahora.astimezone(zona(self.cfg)).date() > date.fromisoformat(vence)
        except ValueError:
            return False

    def historia_de_noticia(self, g, post: dict, foto, jpg: bytes | None) -> None:
        """La noticia recién publicada, como historia en @comunidad_inversores_ok, con foto real o logo oficial."""
        cfg_h = (self.cfg.get("instagram") or {}).get("historias") or {}
        if not (self.historias_activas() and verdadero(cfg_h.get("noticias_comunidad", True))) or en_silencio(self.cfg, self.ahora):
            return
        try:
            foto = foto or self.imagen_para(g, post)
            datos, es_logo, credito = None, False, ""
            if foto is not None and foto.imagen is not None:
                es_logo = bool(foto.es_logo or foto.origen == "marca")
                b = io.BytesIO()
                if es_logo:
                    foto.imagen.convert("RGBA").save(b, "PNG")
                else:
                    foto.imagen.convert("RGB").save(b, "JPEG", quality=90)
                    credito = foto.credito()
                datos = b.getvalue()
            resumen = str(post.get("resumen") or "")
            bajada = re.split(r"(?<=[.!?])\s", resumen)[0][:170] if resumen else ""
            pagina = historias.html_noticia(post.get("titular", ""), bajada, datos, es_logo, credito, g.categoria,
                                            g.urgente, post.get("fuente", ""), "@comunidad_inversores_ok",
                                            # Un solo llamado por historia, rotando: una lleva a Telegram, la siguiente a X.
                                            red=("telegram", "x")[int(self.estado.datos["ig"].get("historias", {}).get("vuelta_red", 0)) % 2])
            self.estado.datos["ig"].setdefault("historias", {})["vuelta_red"] = int(self.estado.datos["ig"]["historias"].get("vuelta_red", 0)) + 1
            self.subir_historia("comunidad", pagina, "noticia")
        except Exception as error:
            log.warning("historia de la noticia: %s", error)

    def tal_vez_comentarios_ig(self) -> None:
        """A quien comenta la palabra de un carrusel (ej. BITCOIN) le manda la guía por DM y le responde el comentario."""
        g = self.estado.datos["ig"]
        guias = {ig_plan.sin_tildes(k).upper(): v for k, v in ((self.cfg.get("instagram") or {}).get("guias") or {}).items()}
        t = instagram.llave("ezebagnato", g)
        if not (guias and t):
            return
        recientes = []
        for h in g.get("historial") or []:
            try:
                if h.get("media_id") and (self.ahora - datetime.fromisoformat(h["fecha"])).days <= 7:
                    recientes.append(h)
            except (KeyError, ValueError):
                continue
        if not recientes:
            return
        try:
            yo = instagram.quien_soy(t)
        except instagram.ErrorInstagram as error:
            log.warning("Instagram: %s", error)
            return
        hechos = set(g.get("respondidos") or [])
        pendientes_a_mano, enviados = [], 0
        for h in recientes:
            palabra = ig_plan.sin_tildes(str(h.get("palabra") or "")).upper()
            guia = guias.get(palabra)
            if not guia:
                continue
            try:
                lista = instagram.comentarios(t, h["media_id"])
            except instagram.ErrorInstagram as error:
                log.warning("Instagram: no pude leer comentarios: %s", error)
                continue
            for c in lista:
                if c["id"] in hechos or c.get("username") == yo.get("username") or enviados >= 25:
                    continue
                if not re.search(rf"\b{re.escape(palabra)}\b", ig_plan.sin_tildes(str(c.get("text") or "")).upper()):
                    continue
                hechos.add(c["id"])
                texto_dm = str(guia.get("mensaje") or "¡Hola! Acá tenés la guía 👇\n\n{link}").format(link=guia.get("link", ""))
                try:
                    instagram.mensaje_privado(t, yo["user_id"], c["id"], texto_dm)
                    respuesta = str(guia.get("respuesta") or "¡Listo! Te mandé la guía por DM 📩")
                    enviados += 1
                except instagram.ErrorInstagram as error:
                    log.warning("Instagram DM a @%s: %s", c.get("username"), error)
                    pendientes_a_mano.append(c.get("username", "?"))
                    respuesta = str(guia.get("respuesta_sin_dm") or "¡Gracias! En un rato te la mando por DM 📩")
                try:
                    instagram.responder_comentario(t, c["id"], respuesta)
                except instagram.ErrorInstagram as error:
                    log.warning("Instagram: no pude responder el comentario: %s", error)
        g["respondidos"] = list(hechos)[-2000:]
        self.estado.guardar()
        if pendientes_a_mano:
            self.avisar("ig_dm", "Instagram no me dejó mandar la guía por DM (seguramente falta que Meta apruebe la app). "
                        "Mandásela vos a: " + ", ".join("@" + u for u in pendientes_a_mano[:30])
                        + f"\n\nLink: {next(iter(guias.values())).get('link', '')}", cada_horas=3)

    def mandar_ranking_ig(self) -> None:
        con = [h for h in (self.estado.datos["ig"].get("historial") or []) if h.get("numeros")]
        if len(con) < 2:
            return
        def tabla(campo: str) -> list[str]:
            grupos: dict[str, list[dict]] = {}
            for h in con:
                grupos.setdefault(h.get(campo, "?"), []).append(h["numeros"])
            filas = []
            for nombre, ns in grupos.items():
                inter = sum(n.get("total_interactions", 0) for n in ns) / len(ns)
                guard = sum(n.get("saved", 0) for n in ns) / len(ns)
                alc = sum(n.get("reach", 0) for n in ns) / len(ns)
                filas.append((inter, f"• {nombre}: {inter:.0f} interacciones, {guard:.0f} guardados, {alc:.0f} de alcance (promedio de {len(ns)})"))
            return [f for _, f in sorted(filas, reverse=True)]
        mejor = max(con, key=lambda h: h["numeros"].get("total_interactions", 0))
        texto = ("📊 Instagram: qué funciona más\n\nPor estilo:\n" + "\n".join(tabla("estilo"))
                 + "\n\nPor tipo de contenido:\n" + "\n".join(tabla("tipo"))
                 + f"\n\nEl mejor hasta ahora: «{mejor.get('tema')}» ({mejor.get('estilo')}).\nSe siguen rotando todos; esto es para que veas la tendencia.")
        try:
            telegram_mensaje(self.token, self.chat_prueba, texto)
        except ErrorPublicacion:
            pass

    def publicar_material(self, tipo: str, notas: str, fotos: list[str]) -> bool:
        partes, correccion = None, ""
        for _ in range(2):
            crudo = ia.redactar_material(self.cerebro, tipo, notas, bool(fotos), correccion)
            candidatas = [sin_links(str(x)).strip() for x in (crudo.get("partes") or []) if str(x).strip()]
            inventados = numeros_inventados(" ".join(candidatas), notas + " " + " ".join(str(i) for i in range(1, 10)), anio_actual(self.ahora))
            plata = any(re.search(r"(?i)\bplata\b", x) for x in candidatas)
            malas = [m for x in candidatas for m in promesas_charla(x)]
            limite = 1000 if tipo == "post" and x_premium(self.cfg["x"]) else 280
            largas = [x for x in candidatas if peso_x(x) > limite]
            if candidatas and not (inventados or plata or malas or largas):
                partes = candidatas if tipo == "hilo" else candidatas[:1]
                break
            correccion = "tu intento anterior no sirve: " + "; ".join(filter(None, [
                f"cifras que no están en las notas ({', '.join(inventados)})" if inventados else "",
                'dice "plata"' if plata else "", f"suena a promesa ({', '.join(malas)})" if malas else "",
                "alguna parte es demasiado larga" if largas else ""])) + "."
        if partes is None:
            telegram_mensaje(self.token, self.chat_prueba, f"❌ No pude armar el {tipo} con lo que me mandaste (la IA no lo escribió bien dos veces). Probá con más detalle en las notas.")
            return False
        imagenes = [telegram_bajar(self.token, f) for f in fotos[:4]]
        imagenes = [i for i in imagenes if i]
        anterior, primero = "", ""
        for i, parte in enumerate(partes):
            jpg = imagenes[i] if i < len(imagenes) else None
            try:
                anterior = x_publicar(self.claves_x, jpg, parte, en_respuesta_a=anterior)
            except ErrorPublicacion as error:
                telegram_mensaje(self.token, self.chat_prueba, f"❌ Se cortó al publicar la parte {i + 1}: {error}")
                break
            primero = primero or anterior
        if primero:
            self.estado.agregar_charla({"fecha": self.ahora.isoformat(), "formato": "material", "tema": notas[:80], "texto": "\n\n".join(partes), "x": primero})
            telegram_mensaje(self.token, self.chat_prueba, f"✅ Publiqué tu {tipo} en X: https://x.com/i/status/{primero}")
        return bool(primero)

    def muestra_grok(self) -> int:
        """Modo prueba: los temas de hoy y los virales, a tu privado."""
        if not grok.disponible():
            self.avisar("grok", "Falta la llave de Grok (XAI_API_KEY) en GitHub.")
            return self.cerrar(0)
        try:
            temas, ganchos = grok.temas_del_dia(self.cfg, self.ahora)
        except grok.SinGrok as error:
            self.avisar("grok", f"Grok {error}.")
            return self.cerrar(0)
        if self.token and self.chat_prueba:
            lineas = ["🧠 <b>De esto se habla hoy en X</b> (las preguntas del día van a salir sobre estos temas):", ""]
            lineas += [f"• <b>{html.escape(t['tema'], quote=False)}</b>: {html.escape(t['contexto'], quote=False)}" for t in temas]
            telegram_mensaje(self.token, self.chat_prueba, "\n".join(lineas), html=True)
        return self.cerrar(1 + (1 if self.mandar_virales() else 0) + (1 if self.mandar_estrategias() else 0))

    def tal_vez_calendario(self) -> bool:
        """La agenda del día (a la mañana) y el aviso un rato antes de cada dato importante."""
        c = self.cfg.get("calendario") or {}
        if not verdadero(c.get("activa")) or not self.canales or en_silencio(self.cfg, self.ahora):
            return False
        memoria = self.estado.datos["calendario"]
        avisados = memoria.setdefault("avisos", {})
        local = self.ahora.astimezone(zona(self.cfg))
        hora_agenda = a_hora(c.get("agenda_hora") or "07:30")
        toca_agenda = memoria.get("agenda") != local.date().isoformat() and hora_agenda is not None and local.time() >= hora_agenda
        eventos = cal.leer(self.cfg, self.ahora)
        if not eventos:
            return False
        hoy = cal.de_hoy(eventos, self.cfg, self.ahora)
        publicado = False
        if toca_agenda:
            memoria["agenda"] = local.date().isoformat()
            futuros = [e for e in hoy if e["fecha"] > self.ahora]
            if futuros:
                tg, x = cal.agenda(futuros)
                self.publicar_suelto(tg, x, hashtags_para(cal.para_hashtags(futuros), self.cfg.get("hashtags") or []))
                log.info("Calendario: agenda del día con %d datos", len(futuros))
                publicado = True
                # Lo que sale dentro de la próxima hora ya quedó avisado en la agenda.
                for e in cal.proximos(eventos, self.ahora):
                    avisados[e["clave"]] = self.ahora.isoformat()
        if not publicado:
            ya_avisados = set(avisados)
            pronto = [e for e in cal.proximos(eventos, self.ahora, int(c.get("aviso_desde_min", 10)), int(c.get("aviso_hasta_min", 80)))
                      if e["clave"] not in ya_avisados]
            if pronto:
                tg, x = cal.aviso_previo(pronto, self.ahora)
                self.publicar_suelto(tg, x, hashtags_para(cal.para_hashtags(pronto), self.cfg.get("hashtags") or []))
                for e in pronto:
                    avisados[e["clave"]] = self.ahora.isoformat()
                log.info("Calendario: aviso previo de %s", ", ".join(e["nombre"] for e in pronto))
                publicado = True
        # La memoria del calendario se poda: solo hace falta la última semana.
        limite = (self.ahora - timedelta(days=8)).isoformat()
        memoria["avisos"] = {k: v for k, v in avisados.items() if str(v) >= limite}
        self.estado.guardar()
        return publicado

    def muestra_calendario(self) -> int:
        """Modo prueba: te manda por privado la agenda y un aviso de ejemplo con los datos reales de la semana."""
        eventos = cal.leer(self.cfg, self.ahora)
        if not eventos:
            self.avisar("calendario", "No pude leer el calendario económico (no respondió o no hay datos importantes esta semana).")
            return self.cerrar(0)
        hoy = cal.de_hoy(eventos, self.cfg, self.ahora)
        futuros = [e for e in eventos if e["fecha"] > self.ahora]
        lista, titulo = (hoy, "Agenda económica de hoy") if hoy else (futuros, "Próximos datos importantes")
        hechas = 0
        if lista and self.token and self.chat_prueba:
            tg, x = cal.agenda(lista, titulo)
            telegram_mensaje(self.token, self.chat_prueba, "📅 Así saldría la agenda en Telegram:\n\n" + tg, html=True)
            hechas += 1
        if futuros and self.token and self.chat_prueba:
            primero = futuros[0]["fecha"]
            juntos = [e for e in futuros if e["fecha"] == primero]
            tg, x = cal.aviso_previo(juntos, primero - timedelta(minutes=60))
            telegram_mensaje(self.token, self.chat_prueba, "⏰ Así saldría el aviso 1 hora antes:\n\n" + tg, html=True)
            telegram_mensaje(self.token, self.chat_prueba, "Y así en X:\n\n" + cal.limpiar_x(x))
            hechas += 1
        return self.cerrar(hechas)

    def publicar_charla(self, forzado: str = "") -> bool:
        """Escribe una charla para X (la IA, con controles), la publica y abajo responde con la invitación
        a seguirte. En modo prueba te la manda por privado."""
        ch = self.cfg.get("x_charla") or {}
        anteriores = self.estado.charlas_recientes()
        self._muestras_charla = getattr(self, "_muestras_charla", -1) + 1      # en una prueba, cada muestra distinta
        numero = len(self.estado.datos["charlas"]) + (0 if self.real else int(self.ahora.timestamp()) // 3600 + self._muestras_charla)
        formatos = [str(f) for f in (ch.get("formatos") or ia.FORMATOS_CHARLA)]
        temas = [str(t) for t in (ch.get("temas") or ia.TEMAS_CHARLA)]
        formato, tema = forzado or formatos[numero % len(formatos)], temas[(numero * 7 + 3) % len(temas)]
        datos, publicidad, caliente = "", False, None
        if formato not in ("venta", "ensenanza"):
            # Si Grok encontró de qué se habla hoy en X, la pregunta va sobre eso.
            caliente = self.tema_caliente()
            if caliente:
                tema = caliente["tema"]
                datos = f"CONTEXTO: {caliente.get('contexto', '')}\nHASHTAGS: sí (según X, el más usado para este tema es: {caliente.get('hashtag') or 'ninguno'})"
        hechas = sum(1 for c in self.estado.datos["charlas"] if isinstance(c, dict) and c.get("formato") == formato)
        hechas += 0 if self.real else self._muestras_charla
        if formato == "ensenanza":
            # Las preguntas que más se buscan en Google, de a una y sin repetir hasta dar la vuelta.
            lista = [t for t in ((self.cfg.get("ensenanza") or {}).get("temas") or []) if t]
            if not lista:
                return False
            elegido = lista[hechas % len(lista)]
            tema = str(elegido.get("pregunta") if isinstance(elegido, dict) else elegido)
            datos = str(elegido.get("datos") or "") if isinstance(elegido, dict) else ""
        elif formato == "venta":
            lista = [p for p in (ch.get("ventas") or []) if isinstance(p, dict) and p.get("producto")]
            if lista:
                elegido = lista[hechas % len(lista)]
                tema, datos = str(elegido["producto"]), str(elegido.get("datos") or "")
            else:
                formato = "pregunta"
        # Lo corto entra en cualquier cuenta; lo largo necesita X Premium (la enseñanza sale entera en Telegram igual).
        premium = x_premium(self.cfg["x"])
        limite = {"largo": 1500, "cuentas": 600, "ensenanza": 1300}.get(formato, 280) if premium else 280
        if formato == "ensenanza":
            limite = 1300
        elif limite == 280 and formato in ("largo", "cuentas"):
            formato = "dilema"
        if formato == "lista":
            limite = 1000 if premium else 280
            if not premium:
                formato = "pregunta"
        texto, correccion, problemas, encuesta = "", "", [], []
        for _ in range(2):
            ganchos = [] if formato in ("venta", "ensenanza") else list(self.estado.datos["grok"].get("ganchos") or [])
            crudo = ia.redactar_charla(self.cerebro, formato, tema, anteriores, correccion, datos, ganchos)
            texto, problemas = revisar_charla(crudo, anteriores, limite)
            if formato == "encuesta":
                encuesta = [sin_links(str(o)).strip() for o in (crudo.get("opciones") or []) if str(o).strip()][:4]
                if not 2 <= len(encuesta) <= 4 or any(len(o) > 25 for o in encuesta):
                    problemas.append("las opciones de la encuesta tienen que ser de 2 a 4, de hasta 25 caracteres cada una")
                if any(re.search(r"(?i)\bplata\b", o) for o in encuesta):
                    problemas.append('una opción dice "plata": tiene que decir "dinero"')
            if not problemas:
                break
            correccion = "tu intento anterior no sirve: " + "; ".join(problemas) + "."
        if problemas:
            log.warning("  la charla no pasó los controles: %s", problemas)
            self.fallas += 1
            return False
        opciones = ch.get("respuestas") or ch.get("respuesta") or []
        opciones = [opciones] if isinstance(opciones, str) else [str(o).strip() for o in opciones if str(o).strip()]
        respuesta = opciones[numero % len(opciones)] if opciones else ""        # va rotando: la misma siempre cansa
        # Hashtags: los del tema caliente (Grok) y los de las personas, empresas y monedas que nombra. Como mucho 2.
        tags = unir_hashtags(hashtags_de_charla(crudo) if datos.startswith("CONTEXTO:") else [],
                             hashtags_para({"titular": texto, "resumen": "", "categoria": ""}, self.cfg.get("hashtags") or []))
        texto = sumar_hashtags(texto, tags, None if premium else LIMITE_X)
        etiquetar = cuentas_para(texto + " " + tema, self.cfg.get("hashtags") or [])
        if datos.startswith("CONTEXTO:") and caliente:
            etiquetar += [f"@{c}" for c in caliente.get("cuentas") or [] if f"@{c}" not in etiquetar]
        if formato == "venta":
            # Abajo de una pregunta de venta, la respuesta lleva al producto (es publicidad: X lo marca).
            elegido = next((p for p in (ch.get("ventas") or []) if isinstance(p, dict) and str(p.get("producto")) == tema), {})
            if str(elegido.get("respuesta") or "").strip():
                respuesta, publicidad = str(elegido["respuesta"]).strip(), True
        ensenanza = formato == "ensenanza"
        log.info("Charla en X (%s · %s): %s", formato, tema, texto.replace("\n", " / ")[:120])
        if not self.real:
            if self.token and self.chat_prueba:
                titulo = "📚 Enseñanza para Telegram y X" if ensenanza else "💬 Pregunta para X"
                muestra = f"{titulo} ({formato} · {tema}):\n\n{texto}"
                if encuesta:
                    muestra += "\n\nOpciones de la encuesta:\n" + "\n".join(f"◻️ {o}" for o in encuesta)
                if respuesta:
                    muestra += f"\n\n↳ y abajo, como respuesta:\n\n{respuesta}"
                try:
                    telegram_mensaje(self.token, self.chat_prueba, muestra)
                except ErrorPublicacion as error:
                    log.error("  no pude mandar la muestra: %s", error)
                    self.fallas += 1
            return True
        id_post, id_telegram = "", None
        if ensenanza and "telegram" in self.canales:
            # La enseñanza también va al canal y al tema del grupo, como una noticia más.
            cuerpo = html.escape(texto, quote=False)
            primera, _, resto = cuerpo.partition("\n")
            cuerpo = f"📚 <b>{primera}</b>\n{resto}"
            if self.redes():
                r = self.redes()
                cuerpo += f'\n\n📲 <a href="{html.escape(r["enlace"], quote=True)}">{html.escape(r["texto"], quote=False)}</a>'
            for i, lugar in enumerate([self.destino, *self.tambien_en]):
                try:
                    resultado = telegram_publicar(self.token, lugar, None, cuerpo)
                    id_telegram = id_telegram if i else resultado
                except ErrorPublicacion as error:
                    self.fallas += 1
                    self.avisar(error.clave, str(error))
        if "x" in self.canales:
            try:
                id_post = x_publicar(self.claves_x, None, texto if premium else recortar_x(texto), encuesta=encuesta or None)
            except ErrorPublicacion as error:
                self.fallas += 1
                self.avisar(error.clave, str(error))
        if not id_post and id_telegram is None:
            return False
        if id_post:
            self.avisar_etiquetar(id_post, texto, etiquetar)
        self.estado.agregar_charla({"fecha": self.ahora.isoformat(), "formato": formato, "tema": tema, "texto": texto,
                                    "x": id_post, "telegram": id_telegram})
        if respuesta and id_post:
            try:
                avisar = publicidad and verdadero(self.cfg["x"].get("marcar_publicidad", True))
                x_publicar(self.claves_x, None, respuesta, publicidad=avisar, en_respuesta_a=id_post)
            except ErrorPublicacion as error:      # la pregunta ya salió: esto es un extra
                log.warning("  no pude dejar la respuesta con la invitación: %s", error)
        return True

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
        # Los lugares donde publica: se comprueba que el bot pueda escribir ahí (sin publicar nada).
        for lugar in [self.destino, *self.tambien_en]:
            if lugar:
                linea = telegram_puede_publicar(self.token, lugar)
                lineas.append("• Telegram " + linea)
                (log.error if linea.startswith("❌") else log.info)("Telegram %s", linea)
        # Si están las llaves de X, se comprueban (sin publicar nada).
        if self.claves_x:
            try:
                lineas.append(f"• X: conectado a la cuenta {x_quien_soy(self.claves_x)} ✅")
            except ErrorPublicacion as error:
                lineas.append(f"• X: ❌ {error}")
                log.error("X: %s", error)
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
