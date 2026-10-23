"""Pruebas del bot. Corren sin internet:  python -m pytest -q"""
from __future__ import annotations

import io
import json
from datetime import datetime, timedelta, timezone

import pytest
import requests
import yaml
from PIL import Image

from bot import estado as mod_estado
from bot import ia, imagen, main, publicar, texto
from bot.filtro import (
    Grupo, agrupar, detectar_socio, es_mala_noticia, fichas, nombra_prohibido, parecido, preparar, titulo_descartado, ya_contada,
)
from bot.fuentes import Noticia, interpretar
from bot.util import a_hora, cargar_ajustes, cupo_a_esta_hora, en_silencio, limpiar_html, ocultar
from tests.internet_falso import InternetFalso, _rss

# Viernes 9 de octubre de 2026, 15:00 en Buenos Aires (fuera del horario de silencio)
AHORA = datetime(2026, 10, 9, 18, 0, tzinfo=timezone.utc)


@pytest.fixture
def cfg():
    return cargar_ajustes()


@pytest.fixture
def red(monkeypatch):
    """Internet de mentira: nada sale de la computadora."""
    falso = InternetFalso(AHORA)
    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", falso.enviar)
    monkeypatch.setattr("time.sleep", lambda s: None)
    return falso


@pytest.fixture
def corrida(monkeypatch, tmp_path, red):
    """Devuelve una función que arma una corrida completa en una carpeta temporal."""
    monkeypatch.setattr(mod_estado, "RAIZ", tmp_path)
    monkeypatch.setattr(main, "SALIDA", tmp_path / "salida")
    for clave in ("ANTHROPIC_API_KEY", "GROQ_API_KEY", "OPENAI_API_KEY", "TELEGRAM_DESTINO", "TELEGRAM_CHAT_PRUEBA"):
        monkeypatch.delenv(clave, raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "clave-gemini")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456789:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA")
    for clave in ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_SECRET"):
        monkeypatch.setenv(clave, "x-" + clave.lower())

    def armar(modo="real", cantidad=1, forzar=False, ahora=AHORA, promo="", imagen="placa"):
        c = main.Corrida(modo, cantidad, forzar, promo)
        c.ahora = ahora
        c.cfg["encendido"] = True
        c.cfg["imagen"]["modo"] = imagen             # la mayoría de las pruebas usan la placa armada por el bot
        c.sin_imagen = imagen == "ninguna"
        # Punto de partida fijo para las pruebas, sin depender de qué esté prendido hoy en ajustes.yaml:
        # ninguna promo aprobada (cada prueba prende las que necesita) y los tres socios activos.
        for promo in c.cfg["promos"]:
            promo["activa"] = False
        for socio in c.cfg["socios"].values():
            socio["activo"] = True
        c.cfg["x"]["premium"] = False                # X corto por defecto en las pruebas (las de Premium lo prenden)
        c.destino = "@canal_real"
        c.tambien_en = []                            # solo el canal (la prueba del grupo lo prende)
        c.chat_prueba = "555"
        return c

    return armar


# ===================================================================== textos

def test_sin_links_no_deja_nada_clicable():
    t = texto.sin_links("Mirá https://ejemplo.com/nota y www.otro.com. Según Investing.com y @alguien #Bitcoin")
    assert "http" not in t and "www." not in t and "@" not in t and "#" not in t
    assert not texto.parece_link(t)
    assert "Investing" in t and "Bitcoin" in t


def test_sin_links_no_rompe_cifras_ni_siglas():
    original = "El S&P 500 subió 1,5% a 6.432,10 puntos en EE.UU. y el dólar cerró a $1.485"
    assert texto.sin_links(original) == original
    assert not texto.parece_link(original)


def test_sin_links_arregla_punto_pegado():
    assert texto.sin_links("El dólar subió.Ahora el mercado espera") == "El dólar subió. Ahora el mercado espera"


def test_peso_x_cuenta_emojis_doble():
    assert texto.peso_x("hola") == 4
    assert texto.peso_x("año") == 3
    assert texto.peso_x("🚨") == 2
    assert texto.peso_x("🇺🇸") == 2
    assert texto.peso_x("⚖️") == 2


def test_numeros_inventados():
    fuente = "Outflows of $1.2 billion; BTC at 83,000; rate cut of 25 basis points to 3.50 percent"
    assert texto.numeros_inventados("Salieron 1,2 mil millones y BTC en 83.000", fuente) == []
    assert texto.numeros_inventados("BTC en 83 mil dólares", fuente) == []
    assert texto.numeros_inventados("La tasa quedó en 3,50", fuente) == []
    assert texto.numeros_inventados("Salieron 1.200 millones", fuente) == []            # la misma cifra, dicha en millones
    assert texto.numeros_inventados("Salieron 12.000 millones", fuente) == ["12.000"]
    assert texto.numeros_inventados("Subió 7% en 2026", fuente, {"2026"}) == ["7"]


def test_las_cifras_se_comparan_por_valor():
    # Un punto o una coma corridos cambian la noticia: no pueden pasar.
    assert texto.numeros_inventados("Bitcoin cayó 15% en un día", "Bitcoin fell 1.5% today") == ["15"]
    assert texto.numeros_inventados("La inflación fue de 21%", "La inflación fue de 2,1% en septiembre") == ["21"]
    assert texto.numeros_inventados("El dólar cerró a $14,85", "El dólar cerró a $1.485") == ["14,85"]
    assert texto.numeros_inventados("La tasa quedó en 350%", "to 3.50 percent") == ["350"]
    assert texto.numeros_inventados("Bitcoin superó los 83.000.000", "Bitcoin at 83") == ["83.000.000"]
    assert texto.numeros_inventados("Bitcoin en 83", "Bitcoin at 83,000") == ["83"]
    # Lo mismo escrito de otra forma sí pasa.
    assert texto.numeros_inventados("Cayó 1,5% y cerró en 6.432,10", "fell 1.5% to 6,432.10") == []
    assert texto.numeros_inventados("Robaron USD 351 millones", "hackers stole $351M from the exchange") == []
    assert texto.numeros_inventados("Tiene 300 millones de usuarios y 83k empleados", "more than 300 million users, 83,000 staff") == []


def _post(**cambios):
    base = {
        "titular": "La Fed recortó la tasa de interés 25 puntos básicos", "titular_imagen": "La Fed bajó la tasa",
        "resumen": "La Reserva Federal bajó la tasa de referencia 25 puntos básicos.", "dato_clave": "El recorte fue de 25 puntos básicos.",
        "por_que_importa": "Una tasa más baja suele abaratar el crédito.", "pregunta": "¿Vos lo esperabas?", "bandera": "🇺🇸",
        "tono": "alcista", "fuente": "Reuters", "fotos": ["Jerome Powell"],
    }
    base.update(cambios)
    return texto.normalizar_post(base, "ECONOMÍA", "Agencias")


def test_texto_x_siempre_entra():
    assert texto.peso_x(texto.texto_x(_post(), True)) <= texto.LIMITE_X
    largo = _post(titular="Palabra " * 60, dato_clave="Dato " * 40, pregunta="¿" + "Pregunta " * 20 + "?")
    resultado = texto.texto_x(largo, True)
    assert texto.peso_x(resultado) <= texto.LIMITE_X
    assert resultado.endswith("Fuente: Reuters")
    assert not texto.parece_link(resultado)


def test_texto_telegram_entra_y_escapa_html():
    p = _post(resumen="Ganancia <b>falsa</b> & " + "muy largo " * 200)
    t = texto.texto_telegram(p, True)
    visible = t.replace("<b>", "").replace("</b>", "")
    assert len(visible) <= 1024
    assert "&lt;b&gt;falsa" in t and "&amp;" in t
    assert "ÚLTIMA HORA" in t and "Fuente: Reuters" in t


def test_normalizar_post_limpia_lo_que_manda_la_ia():
    p = _post(fuente="Investing.com", bandera="🚀🚀", tono="eufórico", titular="Mirá https://x.com/algo la nota.")
    assert p["fuente"] == "Investing"
    assert p["bandera"] == "" and p["tono"] == "neutral"
    assert "http" not in p["titular"] and not p["titular"].endswith(".")
    assert _post(fuente="lanacion.com.ar")["fuente"] == "Lanacion"
    assert _post(fuente="es.investing.com")["fuente"] == "Investing"
    for igual in ("Reserva Federal (Fed)", "The Wall Street Journal", "Bloomberg Línea", "France 24", "YPF S.A.", "Ámbito", "U.S. Treasury"):
        assert _post(fuente=igual)["fuente"] == igual, igual
    assert _post(fuente="")["fuente"] == "Agencias"


def test_con_victimas_no_hay_pregunta():
    p = _post(titular="Un ataque a un buque dejó tres muertos en el estrecho", pregunta="¿Vos qué opinás? Votá ✅ o ❌")
    assert p["pregunta"] == ""
    assert "Votá" not in texto.texto_x(p, False) and "Votá" not in texto.texto_telegram(p, False)
    assert _post(pregunta="¿Vos lo esperabas?")["pregunta"] == "¿Vos lo esperabas?"


def test_limpiar_html_y_ocultar():
    assert limpiar_html("<p>Hola&nbsp;<b>mundo</b></p><script>x()</script> Leer más") == "Hola mundo"
    tapado = ocultar("fallo en https://api.telegram.org/bot123456789:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA/sendPhoto con clave-secreta", "clave-secreta")
    assert "AAAA" not in tapado and "clave-secreta" not in tapado


# ===================================================================== filtro

def _n(titulo, fuente="Ámbito", minutos=10, peso=1.0, resumen="", cobertura=1):
    return Noticia(id=titulo[:12] + fuente, titulo=titulo, resumen=resumen, enlace="", fuente=fuente,
                   fecha=AHORA - timedelta(minutes=minutos), peso=peso, cobertura=cobertura)


def test_agrupa_la_misma_noticia_de_dos_medios():
    grupos = agrupar([
        _n("El BCRA compró USD 150 millones y las reservas superan los USD 40.000 millones", "Ámbito", peso=1.3),
        _n("El BCRA compró USD 150 millones y las reservas superan los USD 40.000 millones otra vez", "Infobae"),
        _n("Bitcoin cae 5% tras los datos de inflación de Estados Unidos", "CriptoNoticias"),
    ])
    assert len(grupos) == 2
    assert grupos[0].principal.fuente == "Ámbito" and grupos[0].fuentes == ["Ámbito", "Infobae"]
    assert parecido(fichas("La Fed baja la tasa"), fichas("Messi hizo tres goles")) == 0


def test_no_nombrar():
    lista = ["eToro", "XM", "Capital.com", "Pepperstone", "Deriv"]
    assert nombra_prohibido("eToro lanza copy trading", lista) == "eToro"
    assert nombra_prohibido("La app de ETORO sumó usuarios", lista) == "eToro"
    assert nombra_prohibido("XM abre oficina en Chile", lista) == "XM"
    assert nombra_prohibido("El grupo SiriusXm presentó resultados", lista) is None
    assert nombra_prohibido("Los derivados financieros crecieron", lista) is None
    assert nombra_prohibido("Capital.com suma clientes", lista) == "Capital.com"
    assert nombra_prohibido("La Fed baja la tasa y sube Wall Street", lista) is None


def test_titulos_descartados(cfg):
    p = cfg["titulos_descartados"]
    assert titulo_descartado("ANSES: cuándo cobro la jubilación de octubre", p)
    assert titulo_descartado("Dólar hoy y dólar blue hoy minuto a minuto", p)
    assert titulo_descartado("Manual del plazo fijo: ¿qué es la TNA?", p)
    assert titulo_descartado("My husband inherited $3 million. Who's right?", p)
    assert not titulo_descartado("La Fed recortó la tasa de interés 25 puntos básicos", p)
    assert not titulo_descartado("El petróleo salta tras un nuevo ataque a un buque cisterna", p)


def test_preparar_descarta_viejas_guias_y_prohibidas(cfg):
    cfg["no_nombrar"] = ["Pepperstone"]
    grupos, cuenta = preparar([
        _n("La Fed recortó la tasa de interés 25 puntos básicos", "Reuters", peso=1.5),
        _n("Noticia de hace un día que ya no va", minutos=60 * 24),
        _n("ANSES: cuándo cobro la jubilación de octubre"),
        _n("Pepperstone lanza una nueva plataforma en la región"),
    ], cfg, AHORA)
    assert [g.principal.titulo for g in grupos] == ["La Fed recortó la tasa de interés 25 puntos básicos"]
    assert cuenta["viejas"] == 1 and cuenta["descartadas"] == 1 and cuenta["prohibidas"] == 1


def test_ya_contada():
    g = agrupar([_n("La Fed recortó la tasa de interés 25 puntos básicos")])[0]
    assert ya_contada(g, ["La Fed recortó la tasa de interés en 25 puntos básicos"])
    assert not ya_contada(g, ["El BCRA compró USD 150 millones"])


def test_silencio(cfg):
    assert not en_silencio(cfg, AHORA)                                             # 15:00
    assert en_silencio(cfg, datetime(2026, 10, 9, 6, 0, tzinfo=timezone.utc))     # 03:00
    assert not en_silencio(cfg, datetime(2026, 10, 9, 10, 0, tzinfo=timezone.utc))  # 07:00


# ==================================================================== fuentes

def test_interpretar_feed_comun_y_google():
    comun = _rss("Medio", [{"title": "La Fed recortó la tasa de interés", "fecha": AHORA, "desc": "<p>El comité bajó <b>25</b> puntos.</p> Leer más"}])
    n = interpretar(comun, {"nombre": "Medio", "url": "https://medio.test/rss", "idioma": "es", "peso": 1.2}, AHORA)[0]
    assert n.titulo == "La Fed recortó la tasa de interés" and n.resumen == "El comité bajó 25 puntos."
    assert n.fecha == AHORA and n.peso == 1.2

    google = _rss("G", [{"title": "La Fed recortó la tasa - Infobae.com", "source": "Infobae.com", "fecha": AHORA,
                         "desc": "<ol><li>a</li><li>b</li><li>c</li></ol>"}])
    g = interpretar(google, {"nombre": "Google Noticias", "url": "https://news.google.com/rss/x"}, AHORA)[0]
    assert g.titulo == "La Fed recortó la tasa" and g.fuente == "Infobae" and g.cobertura == 3 and g.resumen == ""


def test_interpretar_aguanta_feeds_rotos():
    assert interpretar(b"esto no es un feed", {"nombre": "x", "url": "https://x.test"}, AHORA) == []
    assert interpretar(b"", {"nombre": "x", "url": "https://x.test"}, AHORA) == []


# ========================================================================= IA

def test_extraer_json():
    assert ia.extraer_json('{"a": 1}') == {"a": 1}
    assert ia.extraer_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert ia.extraer_json('<think>pienso {x}</think>Acá va: {"a": {"b": 2}} listo') == {"a": {"b": 2}}
    assert ia.extraer_json("no hay nada") is None


def test_cerebro_elige_proveedor_segun_claves(cfg):
    assert not ia.Cerebro(cfg, {}).disponible
    c = ia.Cerebro(cfg, {"GEMINI_API_KEY": "g", "GROQ_API_KEY": "q", "ANTHROPIC_API_KEY": "a"})
    assert [p["nombre"] for p in c.proveedores] == ["anthropic", "gemini", "groq"]


def test_cerebro_pasa_al_siguiente_si_uno_falla(cfg, red, monkeypatch):
    llamadas = []
    original = red._ia

    def con_fallas(pedido, host):
        modelo = json.loads(red._cuerpo(pedido))["model"]
        llamadas.append((host, modelo))
        if host == "generativelanguage.googleapis.com":
            return red._r(pedido, 429, {"error": "cuota"}, {"retry-after": "3600"})
        return original(pedido, host)

    monkeypatch.setattr(red, "_ia", con_fallas)
    c = ia.Cerebro(cfg, {"GEMINI_API_KEY": "g", "GROQ_API_KEY": "q"})
    assert c.pedir_json("Elegís la imagen", "x") == {"elegida": 0}
    assert [h for h, _ in llamadas][-1] == "api.groq.com"
    assert len([1 for h, _ in llamadas if h.startswith("generativelanguage")]) == len(cfg["ia"]["modelos"]["gemini"])


def test_cerebro_clave_mala_y_sin_respuesta(cfg, red):
    red.ia_estado = 401
    with pytest.raises(ia.SinCerebro):
        ia.Cerebro(cfg, {"GEMINI_API_KEY": "mala"}).completar("s", "u")
    assert len(red.llamadas_ia) == 1          # con clave rechazada no insiste con los otros modelos


def test_cerebro_formato_de_cada_servicio(cfg, red):
    ia.Cerebro(cfg, {"GEMINI_API_KEY": "g"}).completar("Elegís la imagen", "u")
    gemini = red.llamadas_ia[-1]
    assert gemini["model"] == "gemini-3.5-flash-lite" and gemini["messages"][0]["role"] == "system"
    assert red.pedidos[-1].url == "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
    assert red.pedidos[-1].headers["Authorization"] == "Bearer g"

    ia.Cerebro(cfg, {"ANTHROPIC_API_KEY": "a"}).completar("Elegís la imagen", "u")
    claude = red.llamadas_ia[-1]
    assert claude["system"] == "Elegís la imagen" and claude["messages"] == [{"role": "user", "content": "u"}]
    assert red.pedidos[-1].url == "https://api.anthropic.com/v1/messages"
    assert red.pedidos[-1].headers["x-api-key"] == "a" and red.pedidos[-1].headers["anthropic-version"] == "2023-06-01"


# ===================================================================== imagen

def test_licencias():
    for buena in ("Public domain", "CC0", "CC BY 2.0", "CC BY-SA 4.0", "PD-USGov", "No restrictions"):
        assert imagen.licencia_libre(buena), buena
    for mala in ("", "CC BY-NC 2.0", "CC BY-ND 4.0", "CC BY-NC-SA 3.0", "Fair use", "GFDL", "Copyrighted"):
        assert not imagen.licencia_libre(mala), mala


def test_busqueda_de_foto_filtra_y_elige(red):
    usadas: set[str] = set()
    f = imagen.buscar_foto(["Jerome Powell"], "La Fed bajó la tasa", lambda t: t in usadas, elegir=lambda t, o: 0)
    assert f.titulo == "File:Jerome_H._Powell.jpg"          # primero la foto principal de Wikipedia
    assert f.imagen.size == (1280, 853) and f.autor == "Federalreserve"
    assert f.credito() == "Foto: Federalreserve · Public domain · Wikimedia Commons"

    # las candidatas nunca incluyen firmas, licencias no comerciales ni fotos chiquitas
    titulos = [c.titulo for c in imagen.candidatas("Jerome Powell")]
    assert titulos == ["File:Jerome_H._Powell.jpg", "File:Foto real.jpg"]

    # si esa foto ya se usó, propone otra
    usadas.add("File:Jerome_H._Powell.jpg")
    otra = imagen.buscar_foto(["Jerome Powell"], "x", lambda t: t in usadas, elegir=lambda t, o: 0)
    assert otra.titulo == "File:Foto real.jpg"

    # si la IA dice que ninguna tiene relación, no se pone foto
    assert imagen.buscar_foto(["Jerome Powell"], "x", lambda t: False, elegir=lambda t, o: -1) is None

    logo = imagen.buscar_foto(["Exness logo"], "x", lambda t: False, elegir=lambda t, o: 0)
    assert logo.es_logo and logo.titulo == "File:Bitcoin logo.svg" and logo.origen == "wikimedia"


def test_logos_guardados_no_necesitan_internet(red):
    binance = imagen.buscar_foto(["Binance logo"], "Binance lanza una tarjeta", lambda t: True, elegir=lambda t, o: 0)
    assert binance.origen == "marca" and binance.es_logo and binance.titulo == "marca:binance.png"
    assert binance.credito() == "" and "LOGO OFICIAL de Binance" in binance.descripcion_corta()
    assert red.pedidos == []                                        # ni un pedido a internet
    assert imagen.logo_guardado("Crypto.com logo").titulo == "marca:crypto-com.png"
    assert imagen.logo_guardado("official Bank of America logo").titulo == "marca:bankofamerica.png"
    assert imagen.logo_guardado("Bitcoin logo").titulo == "marca:bitcoin.png"
    assert imagen.logo_guardado("Gemini logo") is None             # ambiguo (puede ser la IA de Google): no se arriesga
    assert imagen.logo_guardado("Gemini exchange logo").titulo == "marca:gemini-exchange.png"
    assert imagen.logo_guardado("Banco Central logo") is None


def test_foto_no_frena_si_wikimedia_falla(red, monkeypatch):
    monkeypatch.setattr(red, "_wiki", lambda pedido, u, host: red._r(pedido, 429, b"too many requests"))
    assert imagen.buscar_foto(["Jerome Powell"], "x", lambda t: False) is None


@pytest.mark.parametrize("variante,con_foto,urgente", [(0, True, False), (1, True, True), (0, False, True), (1, False, False)])
def test_placa_es_cuadrada_y_liviana(cfg, variante, con_foto, urgente):
    foto = None
    if con_foto:
        foto = imagen.Foto("File:x.jpg", "", 1600, 1000, "image/jpeg", "CC BY 4.0", "Autor", "", "2025", imagen=Image.new("RGB", (1600, 1000), (90, 120, 150)))
    post = _post(titular_imagen="El BCRA compró USD 150 millones y sumó reservas")
    jpg = imagen.armar_placa(post, foto, cfg, AHORA, variante, urgente)
    img = Image.open(io.BytesIO(jpg))
    assert img.size == (1080, 1080) and img.format == "JPEG"
    assert len(jpg) < 5 * 1024 * 1024          # X acepta hasta 5 MB


# =================================================================== publicar

def test_telegram_traduce_los_errores(red):
    token = "123456789:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    red.telegram_error = {"error_code": 401, "description": "Unauthorized"}
    with pytest.raises(publicar.ErrorPublicacion) as e:
        publicar.telegram_foto(token, "@canal", b"jpg", "hola")
    assert e.value.clave == "tg_token" and token not in str(e.value)
    red.telegram_error = {"error_code": 400, "description": "Bad Request: chat not found"}
    with pytest.raises(publicar.ErrorPublicacion) as e:
        publicar.telegram_foto(token, "@canal", b"jpg", "hola")
    assert e.value.clave == "tg_destino"
    red.telegram_error = {"error_code": 403, "description": "Forbidden: bot is not a member of the channel chat"}
    with pytest.raises(publicar.ErrorPublicacion) as e:
        publicar.telegram_foto(token, "@canal", b"jpg", "hola")
    assert e.value.clave == "tg_permiso"


CLAVES_X = {"X_API_KEY": "clave-de-app", "X_API_SECRET": "secreto-de-app", "X_ACCESS_TOKEN": "111-token-de-acceso", "X_ACCESS_SECRET": "secreto-de-acceso"}


def test_x_sube_imagen_y_publica(red):
    claves = CLAVES_X
    assert publicar.x_publicar(claves, b"\xff\xd8jpg", "Hola mundo") == "19000000000000000001"
    assert red.x_posts == [{"text": "Hola mundo", "media": {"media_ids": ["1880000000000000001"]}}]
    assert len(red.x_subidas) == 1


def test_x_traduce_los_errores(red):
    claves = CLAVES_X
    for estado, cuerpo, clave in (
        (401, {"title": "Unauthorized"}, "x_claves"),
        (402, {"title": "CreditsDepleted", "detail": "Your account has no credits"}, "x_credito"),
        (403, {"detail": "Your client app is not configured with the appropriate oauth1 app permissions for this endpoint."}, "x_permiso"),
        (403, {"detail": "You are not allowed to create a Tweet with duplicate content."}, "x_repetido"),
        (429, {"title": "Too Many Requests"}, "x_limite"),
    ):
        red.x_estado, red.x_cuerpo = estado, cuerpo
        with pytest.raises(publicar.ErrorPublicacion) as e:
            publicar.x_publicar(claves, b"jpg", "Hola")
        assert e.value.clave == clave, (estado, e.value.clave)


def test_chats_de_telegram(red):
    chats = publicar.telegram_chats("123456789:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA")
    assert {c["id"] for c in chats} == {555, -100123}


# ========================================================= el bot de punta a punta

def test_corrida_real_publica_la_mas_importante(corrida, red, tmp_path):
    c = corrida()
    c.cfg["no_nombrar"] = ["eToro"]
    assert c.correr() == 0
    # 1) eligió la de la Fed (impacto 9) y la publicó en los dos lados
    assert len(red.telegram_fotos) == 1 and len(red.x_posts) == 1
    tg = red.telegram_fotos[0]
    assert tg["chat_id"] == "@canal_real" and tg["parse_mode"] == "HTML"
    assert "ÚLTIMA HORA" in tg["caption"] and "La Fed recortó la tasa de interés 25 puntos básicos" in tg["caption"]
    assert "Por qué importa" in tg["caption"] and "Fuente: Reserva Federal (Fed)" in tg["caption"]
    x = red.x_posts[0]["text"]
    assert x.startswith("🚨 ÚLTIMA HORA: 🇺🇸 La Fed recortó") and x.endswith("Fuente: Reserva Federal (Fed)")
    assert texto.peso_x(x) <= 280 and not texto.parece_link(x)
    # 2) la imagen quedó guardada y es cuadrada
    assert Image.open(tmp_path / "salida" / "01.jpg").size == (1080, 1080)
    # 3) la memoria anota lo publicado y deja las otras dos en espera
    memoria = json.loads((tmp_path / "estado.json").read_text(encoding="utf-8"))
    assert len(memoria["publicadas"]) == 1 and memoria["publicadas"][0]["x"] and memoria["publicadas"][0]["telegram"]
    assert memoria["publicadas"][0]["foto"] == "File:Jerome_H._Powell.jpg"
    assert sorted(p["categoria"] for p in memoria["pendientes"]) == ["ARGENTINA", "CRIPTO"]
    # 4) nunca vio la noticia de eToro, la de ANSES ni la vieja
    enviado = json.dumps([l["messages"][1]["content"] for l in red.llamadas_ia], ensure_ascii=False)
    assert "eToro" not in enviado and "ANSES" not in enviado and "vieja" not in enviado


def test_no_repite_y_respeta_la_espera(corrida, red):
    assert corrida().correr() == 0
    # a los 10 minutos: todavía no pasó el tiempo mínimo entre publicaciones
    assert corrida(ahora=AHORA + timedelta(minutes=10)).correr() == 0
    assert len(red.telegram_fotos) == 1
    # una hora después publica la siguiente (BCRA, impacto 8), no repite la Fed
    red.ahora = AHORA + timedelta(minutes=61)
    assert corrida(ahora=AHORA + timedelta(minutes=61)).correr() == 0
    assert len(red.telegram_fotos) == 2
    assert "BCRA" in red.telegram_fotos[1]["caption"] and "ÚLTIMA HORA" not in red.telegram_fotos[1]["caption"]
    assert "Fuente: Ámbito" in red.telegram_fotos[1]["caption"]
    # y otra hora después, la tercera (ETF, impacto 7)
    assert corrida(ahora=AHORA + timedelta(minutes=122)).correr() == 0
    assert len(red.telegram_fotos) == 3 and "ETF" in red.telegram_fotos[2]["caption"]
    # ya no queda nada nuevo: no publica más ni gasta llamadas a la IA
    antes = len(red.llamadas_ia)
    assert corrida(ahora=AHORA + timedelta(minutes=183)).correr() == 0
    assert len(red.telegram_fotos) == 3 and len(red.llamadas_ia) == antes


def test_tope_diario(corrida, red):
    c = corrida()
    c.cfg["ritmo"]["max_por_dia"] = 1
    assert c.correr() == 0
    c2 = corrida(ahora=AHORA + timedelta(hours=2))
    c2.cfg["ritmo"]["max_por_dia"] = 1
    assert c2.correr() == 0
    assert len(red.telegram_fotos) == 1


def test_de_madrugada_solo_sale_lo_muy_fuerte(corrida, red, monkeypatch):
    madrugada = datetime(2026, 10, 9, 6, 0, tzinfo=timezone.utc)           # 03:00 en Buenos Aires
    falso = InternetFalso(madrugada)
    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", falso.enviar)
    del falso.feeds["federalreserve.gov"], falso.feeds["news.google.com"]  # sin la noticia de impacto 9
    assert corrida(ahora=madrugada).correr() == 0
    assert falso.telegram_fotos == []                                       # la de impacto 8 espera
    # ...y con la de la Fed (impacto 9) sí sale aunque sea de madrugada
    falso2 = InternetFalso(madrugada)
    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", falso2.enviar)
    assert corrida(ahora=madrugada + timedelta(minutes=5)).correr() == 0
    assert len(falso2.telegram_fotos) == 1 and "Fed" in falso2.telegram_fotos[0]["caption"]


def test_apagado_no_hace_nada(corrida, red):
    c = corrida()
    c.cfg["encendido"] = False
    assert c.correr() == 0
    assert red.pedidos == []


def test_modo_prueba_no_publica_ni_toca_la_memoria(corrida, red, tmp_path):
    c = corrida(modo="prueba", cantidad=2)
    assert c.correr() == 0
    assert red.x_posts == []                                         # nunca a X
    assert [f["chat_id"] for f in red.telegram_fotos] == ["555", "555"]   # solo al chat de prueba
    assert "así saldría en X" in red.telegram_mensajes[0]["text"]
    assert not (tmp_path / "estado.json").exists()
    assert (tmp_path / "salida" / "02.jpg").exists() and (tmp_path / "salida" / "reporte.json").exists()


def test_si_la_ia_inventa_una_cifra_se_corrige_o_no_sale(corrida, red):
    red.cifra_inventada = 1                      # inventa una vez, después corrige
    assert corrida().correr() == 0
    assert len(red.telegram_fotos) == 1 and "número 7" not in red.telegram_fotos[0]["caption"]

    red2_fotos = len(red.telegram_fotos)
    red.cifra_inventada = 99                     # insiste: esa noticia no se publica
    c = corrida(ahora=AHORA + timedelta(minutes=61))
    c.cantidad = 1
    assert c.correr() == 0
    assert red.telegram_fotos[red2_fotos:] == []


def test_si_falla_x_igual_sale_en_telegram_y_avisa_una_sola_vez(corrida, red):
    red.x_estado, red.x_cuerpo = 402, {"title": "CreditsDepleted", "detail": "no credits"}
    assert corrida().correr() == 0
    assert len(red.telegram_fotos) == 1 and red.x_posts == []
    assert len(red.telegram_mensajes) == 1 and "no hay crédito" in red.telegram_mensajes[0]["text"]
    assert red.telegram_mensajes[0]["chat_id"] == "555"
    # una hora después vuelve a fallar X: publica en Telegram pero NO repite el aviso
    assert corrida(ahora=AHORA + timedelta(minutes=61)).correr() == 0
    assert len(red.telegram_fotos) == 2 and len(red.telegram_mensajes) == 1


def test_si_cae_la_ia_avisa_y_no_publica(corrida, red):
    red.ia_estado = 503
    assert corrida().correr() == 0
    assert red.telegram_fotos == [] and red.x_posts == []
    assert "La IA no respondió" in red.telegram_mensajes[0]["text"]


def test_sin_foto_usa_placa_de_texto(corrida, red, tmp_path):
    red.foto_elegida = -1
    assert corrida().correr() == 0
    memoria = json.loads((tmp_path / "estado.json").read_text(encoding="utf-8"))
    assert memoria["publicadas"][0]["foto"] == "" and len(red.telegram_fotos) == 1


def test_modo_chats(corrida, red, caplog):
    import logging
    caplog.set_level(logging.INFO, logger="bot")
    assert corrida(modo="chats").correr() == 0
    assert "id=-100123" in caplog.text and "@canalprueba" in caplog.text


# ============================== lo nuevo: ritmo, socios, llamados, promos e imágenes con IA

def _local(hora: int, minuto: int = 0) -> datetime:
    """Una hora de Buenos Aires del 9 de octubre de 2026, pasada a UTC."""
    return datetime(2026, 10, 9, hora, minuto, tzinfo=timezone.utc) + timedelta(hours=3)


def _sembrar(tmp_path, cuantas: int, tipo: str = "noticia", hace_minutos: int = 120):
    """Deja en la memoria publicaciones hechas más temprano hoy."""
    publicadas = [{
        "fecha": (AHORA - timedelta(minutes=hace_minutos + 10 * i)).isoformat(), "tipo": tipo,
        "titular": f"Publicación anterior número {i} sin relación", "titulos_originales": [], "telegram": 1, "x": "1",
    } for i in range(cuantas)]
    (tmp_path / "estado.json").write_text(json.dumps({"publicadas": list(reversed(publicadas))}), encoding="utf-8")


def _solo(red, titulo: str, resumen: str = "", fuente: str = "coindesk.com"):
    """Deja una sola noticia en el internet de mentira."""
    red.feeds.clear()
    red.feeds[fuente] = _rss("Medio", [{"title": titulo, "fecha": AHORA - timedelta(minutes=20), "desc": resumen or titulo + " Más detalles."}])


def test_cupo_reparte_las_diez_a_lo_largo_del_dia(cfg):
    assert cfg["ritmo"]["max_por_dia"] == 10
    assert cupo_a_esta_hora(cfg, _local(3)) == 1        # madrugada
    assert cupo_a_esta_hora(cfg, _local(7)) == 1        # arranca el día
    assert cupo_a_esta_hora(cfg, _local(12)) == 3
    assert cupo_a_esta_hora(cfg, _local(15)) == 5
    assert cupo_a_esta_hora(cfg, _local(18)) == 7
    assert cupo_a_esta_hora(cfg, _local(23, 30)) == 10
    assert en_silencio(cfg, _local(0, 7)) and en_silencio(cfg, _local(6, 59)) and not en_silencio(cfg, _local(7, 7))


def test_si_el_dia_va_adelantado_solo_pasa_lo_urgente(corrida, red, tmp_path):
    _sembrar(tmp_path, 6)                                # a las 15:00 corresponden 5 y ya van 6
    del red.feeds["federalreserve.gov"], red.feeds["news.google.com"]
    assert corrida().correr() == 0
    assert red.telegram_fotos == []                      # la del BCRA (impacto 8) espera su turno
    memoria = json.loads((tmp_path / "estado.json").read_text(encoding="utf-8"))
    assert len(memoria["pendientes"]) == 2


def test_lo_urgente_sale_aunque_el_dia_vaya_adelantado(corrida, red, tmp_path):
    _sembrar(tmp_path, 6)
    assert corrida().correr() == 0
    assert len(red.telegram_fotos) == 1 and "ÚLTIMA HORA" in red.telegram_fotos[0]["caption"]


def test_detecta_a_los_socios_y_las_malas_noticias(cfg):
    assert detectar_socio("Binance lanza una tarjeta física", cfg) == "binance"
    assert detectar_socio("Exness suma nuevos instrumentos | otra cosa", cfg) == "exness"
    assert detectar_socio("El BCRA compró dólares", cfg) == ""
    assert detectar_socio("Los traders de binancecoin", cfg) == ""
    for mala in ("Hackean a Binance", "Binance fined $4 billion", "Multa récord al exchange", "El broker suspende retiros",
                 "Quiebra el exchange", "SEC sues the exchange", "Roban USD 351 millones"):
        assert es_mala_noticia(mala), mala
    for buena in ("Binance lanza una tarjeta física", "Exness suma nuevos instrumentos", "Binance llega a 300 millones de usuarios"):
        assert not es_mala_noticia(buena), buena


def test_noticia_buena_de_un_socio_lleva_el_llamado(corrida, red, tmp_path):
    _solo(red, "Binance lanza una tarjeta física para pagar con cripto en comercios")
    assert corrida().correr() == 0
    pie = red.telegram_fotos[0]["caption"]
    assert '👉 <a href="https://www.binance.com/join?ref=202718448">Abrí tu cuenta en Binance con mi link</a>' in pie
    assert "¿Vos la pedirías?" not in pie                                # el llamado reemplaza a la pregunta
    x = red.x_posts[0]
    assert "👉 Abrí tu cuenta en Binance con mi link (el link está en mi perfil)" in x["text"]
    assert "http" not in x["text"] and texto.peso_x(x["text"]) <= 280
    assert x["paid_partnership"] is True                                 # X exige avisar que hay un link de afiliado
    hecho = json.loads((tmp_path / "estado.json").read_text(encoding="utf-8"))["publicadas"][-1]
    assert hecho["socio"] == "binance" and hecho["llamado"] is True and hecho["foto"] == "marca:binance.png"


def test_noticia_mala_de_un_socio_sale_sin_llamado(corrida, red):
    _solo(red, "Hackean a Binance y suspende los retiros de sus usuarios")
    assert corrida().correr() == 0
    assert "Hackean a Binance" in red.telegram_fotos[0]["caption"]
    assert "con mi link" not in red.telegram_fotos[0]["caption"]
    assert "con mi link" not in red.x_posts[0]["text"] and "paid_partnership" not in red.x_posts[0]


def test_con_links_en_x_el_llamado_lleva_tu_link(corrida, red):
    _solo(red, "Binance lanza una tarjeta física para pagar con cripto en comercios")
    c = corrida()
    c.cfg["x"]["poner_links"] = True
    c.cfg["socios"]["binance"]["enlace"] = "https://accounts.binance.com/register?ref=PRUEBA123"
    assert c.correr() == 0
    x = red.x_posts[0]["text"]
    assert "👉 Abrí tu cuenta en Binance con mi link: https://accounts.binance.com/register?ref=PRUEBA123" in x
    assert texto.peso_x(x) <= 280
    assert 'href="https://accounts.binance.com/register?ref=PRUEBA123"' in red.telegram_fotos[0]["caption"]


def test_socio_sin_link_usa_el_link_general(corrida, red):
    _solo(red, "Binance lanza una tarjeta física para pagar con cripto en comercios")
    c = corrida()
    c.cfg["socios"]["binance"]["enlace"] = ""
    assert c.correr() == 0
    assert '<a href="https://linktr.ee/comunidadinversores">Abrí tu cuenta en Binance con mi link</a>' in red.telegram_fotos[0]["caption"]


def test_tope_de_llamados_por_dia(corrida, red, tmp_path):
    _solo(red, "Binance lanza una tarjeta física para pagar con cripto en comercios")
    c = corrida()
    c.cfg["ritmo"]["max_llamados_por_dia"] = 0
    assert c.correr() == 0
    assert "con mi link" not in red.telegram_fotos[0]["caption"]


def test_promesas():
    assert texto.promesas("Ganancias aseguradas del 20% por mes, sin riesgo") == ["20%", "aseguradas", "ganancias", "sin riesgo"]
    assert texto.promesas("Generá ingresos pasivos y duplicá tu capital") == ["duplica", "ingresos pasivos"]
    assert texto.promesas("Bots que operan solos. Tu plata queda en tu cuenta.") == []
    assert texto.promesas("Con esto no podés perder") == ["no podes perder"]


PROMESAS_DISFRAZADAS = [
    "Con mis bots ganás plata todos los días mientras dormís", "Mis bots generan ganancias todos los meses",
    "Resultados asegurados desde el primer día", "Rendimiento asegurado con mis bots", "Retorno asegurado y sin vueltas",
    "Garantía de ganancias con el copy trading", "Tu capital crece solo, sin ningún riesgo", "Riesgo cero: tu plata trabaja por vos",
    "Hacé crecer tu capital al doble", "Plata segura todos los meses", "Libertad financiera con mis bots",
    "Viví del trading sin esfuerzo", "No hay nada que te duplique el capital como este bot",
    "Nunca una herramienta te multiplicó así el capital", "¿No es dinero fácil? Probalo", "Es imposible perder con esta estrategia",
    "Recuperás tu inversión en un mes", "Ganás sí o sí", "No te pierdas las ganancias aseguradas",
    "No esperes más: ganancias aseguradas", "Volvete millonario con el copy trading", "Te aseguro ganancias desde el primer mes",
]
ADVERTENCIAS_HONESTAS = [
    "Ningún bot garantiza ganancias y esto no es dinero fácil.", "Nadie te puede garantizar resultados. No es una inversión sin riesgo.",
    "No es para quien busca dinero fácil", "Si buscás dinero fácil, esto no es para vos", "Te muestro cómo trabaja, sin garantizarte nada",
    "Los bots no te van a garantizar ganancias", "Nada te garantiza ganancias", "No prometemos rentabilidad",
    "La rentabilidad no está asegurada", "Rentabilidad pasada no garantiza rentabilidad futura", "Operar sin riesgo no existe",
    "Ninguna estrategia está libre de pérdidas ni es sin riesgo", "Evitá órdenes duplicadas con el bot", "No esperes ganancias aseguradas",
    "Esto no elimina el riesgo ni garantiza nada", "No existe el dinero fácil", "Acá nadie promete ingresos pasivos",
    "Ojo: no son ingresos pasivos", "Los bots pueden perder: no hay ganancias aseguradas",
    "Registrate y ganá un bono de bienvenida", "Así ganás tiempo: el bot opera solo",
]


@pytest.mark.parametrize("frase", PROMESAS_DISFRAZADAS)
def test_ninguna_forma_de_prometer_ganancias_pasa(frase):
    assert texto.promesas(frase), frase


@pytest.mark.parametrize("frase", ADVERTENCIAS_HONESTAS)
def test_una_advertencia_honesta_no_es_una_promesa(frase):
    assert texto.promesas(frase) == [], frase


def test_un_porcentaje_solo_vale_si_es_un_dato_del_producto():
    datos = "Promoción: 3% de reintegro en USDC, con tope de 100 USDC por mes. Cashback del 20 % en comisiones."
    for bien in ("Te devuelve 3% de cada compra en USDC, con tope de 100 USDC por mes", "Tenés 20% de cashback en comisiones",
                 "Reintegro del 3 por ciento en cada compra", "Hasta 100 USDC por mes con 3% de reintegro",
                 "Sin costo anual y con 3% de reintegro", "Te vuelve el 3% de lo que gastes"):
        assert texto.promesas(bien, datos) == [], bien
    assert texto.promesas("Te devuelve 5% de cada compra", datos) == ["5%"]           # ese porcentaje no está en los datos
    assert texto.promesas("Te devuelve 3% de cada compra") == ["3%"]                  # sin datos, ningún porcentaje pasa
    assert texto.promesas("Devuelve el tres por ciento", datos) == ["%"]              # un porcentaje sin número tampoco
    for mal in ("Rinde 3% por mes", "Rendimiento mensual del 20%", "Reintegro del 3% por mes", "Rinde 3％ por mes",
                "3% en USDC de pura ganancia", "Con 3% de reintegro tu plata crece", "Ganá un 3% en cada compra"):
        assert texto.promesas(mal, datos), mal                                        # el mismo número, pero como rendimiento


def _con_promos(c, cuantas: int = 2):
    """Aprueba las promos de Pionex (las que usan las pruebas de rotación)."""
    elegidas = ("bots-pionex", "pionex-bienvenida")[:cuantas]
    for p in c.cfg["promos"]:
        p["activa"] = p["id"] in elegidas
    return c


def test_sin_promos_aprobadas_solo_salen_noticias(corrida, red, tmp_path):
    del red.feeds["federalreserve.gov"], red.feeds["news.google.com"]
    assert corrida().correr() == 0
    hecho = json.loads((tmp_path / "estado.json").read_text(encoding="utf-8"))["publicadas"]
    assert [h["tipo"] for h in hecho] == ["noticia"]


def test_la_promo_sale_a_su_hora_y_va_rotando(corrida, red, tmp_path):
    del red.feeds["federalreserve.gov"], red.feeds["news.google.com"]      # sin noticias de última hora
    assert _con_promos(corrida()).correr() == 0                             # 15:00 -> ya pasó el horario de las 10:30
    pie = red.telegram_fotos[0]["caption"]
    assert "✅ Sin configurar nada" in pie and "✅ Tu plata queda en tu cuenta" in pie
    assert '<a href="https://accounts.pionex.com/es/signUp?r=0uCbaWYsxRp">Copiá mis bots en Pionex con mi link</a>' in pie
    assert "<i>Los bots de trading no garantizan ganancias: podés perder capital.</i>" in pie
    x = red.x_posts[0]
    assert x["paid_partnership"] is True and "Copiá mis bots en Pionex con mi link" in x["text"]
    assert "http" not in x["text"] and texto.peso_x(x["text"]) <= 280
    assert Image.open(tmp_path / "salida" / "01.jpg").size == (1080, 1080)
    memoria = json.loads((tmp_path / "estado.json").read_text(encoding="utf-8"))
    assert memoria["publicadas"][0]["tipo"] == "promo" and memoria["publicadas"][0]["promo_id"] == "bots-pionex"
    assert len(memoria["pendientes"]) == 2                                  # las noticias quedaron esperando

    # 16:01 -> pasó una hora desde la promo: todavía no va otra (nunca dos pegadas). Sale una noticia.
    assert _con_promos(corrida(ahora=AHORA + timedelta(minutes=61))).correr() == 0
    memoria = json.loads((tmp_path / "estado.json").read_text(encoding="utf-8"))
    assert [p["tipo"] for p in memoria["publicadas"]] == ["promo", "noticia"]

    # 18:10 -> ya pasaron 3 horas y el horario de las 15:30: sale la OTRA promo
    assert _con_promos(corrida(ahora=_local(18, 10))).correr() == 0
    memoria = json.loads((tmp_path / "estado.json").read_text(encoding="utf-8"))
    assert [p.get("promo_id") for p in memoria["publicadas"] if p["tipo"] == "promo"] == ["bots-pionex", "pionex-bienvenida"]

    # 19:15 -> la promo del horario de las 15:30 ya salió: va una noticia
    assert _con_promos(corrida(ahora=_local(19, 15))).correr() == 0
    # 20:35 -> horario de las 20:30, pero la última promo fue hace menos de 3 horas: espera
    assert _con_promos(corrida(ahora=_local(20, 35))).correr() == 0
    memoria = json.loads((tmp_path / "estado.json").read_text(encoding="utf-8"))
    assert [p["tipo"] for p in memoria["publicadas"]].count("promo") == 2
    # 21:40 -> tercera promo: vuelve a tocar la primera (la que hace más que no sale)
    assert _con_promos(corrida(ahora=_local(21, 40))).correr() == 0
    memoria = json.loads((tmp_path / "estado.json").read_text(encoding="utf-8"))
    assert memoria["publicadas"][-1].get("promo_id") == "bots-pionex"
    # y ya no hay más promos por hoy
    assert _con_promos(corrida(ahora=_local(23, 50))).correr() == 0
    memoria = json.loads((tmp_path / "estado.json").read_text(encoding="utf-8"))
    assert [p["tipo"] for p in memoria["publicadas"]].count("promo") == 3


def test_una_noticia_de_ultimo_momento_le_gana_a_la_promo(corrida, red, tmp_path):
    assert _con_promos(corrida()).correr() == 0
    assert "ÚLTIMA HORA" in red.telegram_fotos[0]["caption"]
    assert _con_promos(corrida(ahora=AHORA + timedelta(minutes=61))).correr() == 0
    memoria = json.loads((tmp_path / "estado.json").read_text(encoding="utf-8"))
    assert [p["tipo"] for p in memoria["publicadas"]] == ["noticia", "promo"]


def test_una_promo_nunca_promete_ganancias(corrida, red, tmp_path):
    del red.feeds["federalreserve.gov"], red.feeds["news.google.com"]
    red.promo_tramposa = 1                       # la IA promete una vez; el control la hace corregir
    assert _con_promos(corrida()).correr() == 0
    assert "asegurad" not in red.telegram_fotos[0]["caption"].lower() and "20%" not in red.telegram_fotos[0]["caption"]

    red.promo_tramposa = 99                      # si insiste, esa promo directamente no sale...
    assert _con_promos(corrida(ahora=_local(18, 10))).correr() == 0       # 18:10: ya toca la promo siguiente
    memoria = json.loads((tmp_path / "estado.json").read_text(encoding="utf-8"))
    assert [p["tipo"] for p in memoria["publicadas"]] == ["promo", "noticia"]   # ...y su lugar lo ocupa una noticia
    assert all("asegurad" not in f["caption"].lower() and "20%" not in f["caption"] for f in red.telegram_fotos)
    assert all("asegurad" not in p["text"].lower() and "20%" not in p["text"] for p in red.x_posts)
    aviso = red.telegram_mensajes[-1]                                      # Eze se entera por su chat privado
    assert aviso["chat_id"] == "555" and "no salió" in aviso["text"] and "promesa de ganancia" in aviso["text"]
    assert "pionex-bienvenida" in memoria["promos"]                        # esa promo pasó al final de la fila

    # Una hora después se prueba con la otra promo. Si la IA ya se porta bien, sale.
    red.promo_tramposa = 0
    assert _con_promos(corrida(ahora=_local(19, 15))).correr() == 0
    memoria = json.loads((tmp_path / "estado.json").read_text(encoding="utf-8"))
    assert [p.get("promo_id") for p in memoria["publicadas"] if p["tipo"] == "promo"] == ["bots-pionex", "bots-pionex"]


def test_muestra_de_promo_rechazada_avisa(corrida, red, tmp_path):
    red.promo_tramposa = 99
    assert corrida(modo="prueba", promo="binance-tarjeta").correr() == 1          # no hay muestra: la corrida queda en rojo
    assert red.telegram_fotos == [] and red.x_posts == []
    assert "no salió" in red.telegram_mensajes[-1]["text"]


def test_muestra_de_promo_en_modo_prueba(corrida, red, tmp_path):
    assert corrida(modo="prueba", promo="bots-pionex").correr() == 0   # aunque esté apagada, se puede ver la muestra
    assert [f["chat_id"] for f in red.telegram_fotos] == ["555"] and red.x_posts == []
    assert "✅" in red.telegram_fotos[0]["caption"]
    assert not (tmp_path / "estado.json").exists()
    assert corrida(modo="prueba", promo="pionex-bienvenida").correr() == 0
    assert len(red.telegram_fotos) == 2


def test_placa_de_promo(cfg):
    promo = {"titular_imagen": "Bots que operan por vos", "puntos": ["Sin configurar nada", "Tu plata queda en tu cuenta", "Operan las 24 horas"]}
    for foto in (None, Image.new("RGB", (1600, 900), (40, 70, 110))):
        jpg = imagen.armar_promo(promo, "MIS HERRAMIENTAS", "Copiá mis bots en Pionex con mi link", "Los bots no garantizan ganancias.", cfg, AHORA, foto)
        assert Image.open(io.BytesIO(jpg)).size == (1080, 1080) and len(jpg) < 5 * 1024 * 1024


def test_placa_de_promo_con_el_logo_de_la_marca(cfg):
    promo = {"titular_imagen": "Pagá con cripto en cualquier comercio", "puntos": ["Sin costo de emisión", "3% de reintegro en USDC"]}
    logo = imagen.logo_guardado("Binance logo")
    con_logo = imagen.armar_promo(promo, "PROMO", "Abrí tu cuenta en Binance con mi link", "", cfg, AHORA, None, logo.imagen)
    sin_logo = imagen.armar_promo(promo, "PROMO", "Abrí tu cuenta en Binance con mi link", "", cfg, AHORA)
    assert Image.open(io.BytesIO(con_logo)).size == (1080, 1080) and con_logo != sin_logo


def test_imagen_hecha_con_ia_como_plan_c(corrida, red, tmp_path):
    red.foto_elegida = -1                        # no hay ninguna foto real que sirva
    c = corrida()
    c.cfg["imagen_ia"]["activa"] = True
    assert c.correr() == 0
    assert len(red.imagenes_ia) == 1 and red.imagenes_ia[0]["model"] == "gemini-3.1-flash-lite-image"
    pedido = red.imagenes_ia[0]["input"][0]["text"]
    assert "no recognizable real people" in pedido and "central bank building" in pedido
    assert red.x_posts[0]["made_with_ai"] is True                         # sale avisada como hecha con IA
    hecho = json.loads((tmp_path / "estado.json").read_text(encoding="utf-8"))["publicadas"][-1]
    assert hecho["foto"].startswith("ia:")


def test_la_imagen_con_ia_esta_apagada_por_defecto(corrida, red):
    red.foto_elegida = -1
    assert corrida().correr() == 0
    assert red.imagenes_ia == [] and "made_with_ai" not in red.x_posts[0]


def test_fuentes_con_plazo_propio(cfg):
    vieja = _n("Exness anuncia una novedad para sus clientes de la región", "Finance Magnates", minutos=60 * 10)
    comun = _n("Otra noticia cualquiera de hace diez horas en el mercado", "Reuters", minutos=60 * 10)
    vieja.horas = 24                              # las de los socios valen un día entero
    grupos, cuenta = preparar([vieja, comun], cfg, AHORA)
    assert [g.principal.fuente for g in grupos] == ["Finance Magnates"] and grupos[0].socio == "exness"
    assert cuenta["viejas"] == 1


def test_la_marca_y_el_logo_estan_en_su_lugar(cfg):
    assert cfg["marca"]["nombre"] == "EZEQUIEL BAGNATO | COMUNIDAD INVERSORES"
    assert cfg["colores"]["bajista"].upper() == "#EF4444"                  # rojo para las malas
    logo = Image.open(imagen.RAIZ / "assets" / "logo.png")
    assert logo.mode == "RGBA" and logo.size == (512, 512)
    assert logo.getpixel((2, 2))[3] == 0 and logo.getpixel((256, 256))[3] == 255   # redondo: esquinas transparentes


def test_los_socios_no_tapan_al_resto_de_las_noticias(corrida, red):
    titulos = [
        "Binance lanza una tarjeta física con devolución en cada compra",
        "El nuevo bot de arbitraje de Binance ya funciona en Latinoamérica",
        "Staking flexible: Binance suma tres monedas al programa",
        "Pagos con QR: Binance amplía el servicio a comercios de Brasil",
        "Binance estrena billetera web sin necesidad de descargar nada",
        "Llegan los futuros de oro tokenizado a Binance",
        "Binance abre un programa de becas para estudiantes de blockchain",
        "Préstamos con garantía cripto: así es la propuesta de Binance",
        "Una versión más liviana de la app de Binance para celulares viejos",
        "Binance refuerza su soporte en español con atención las 24 horas",
    ]
    red.feeds.clear()
    red.feeds["coindesk.com"] = _rss("Medio", [
        {"title": t, "fecha": AHORA - timedelta(minutes=20 + i), "desc": f"Novedad número {i} del exchange."} for i, t in enumerate(titulos)
    ])
    red.feeds["ambito.com"] = _rss("Ámbito", [{"title": "El BCRA compró USD 150 millones y las reservas superan los USD 40.000 millones",
                                              "fecha": AHORA - timedelta(minutes=30), "desc": "Las reservas brutas cerraron en USD 40.120 millones."}])
    c = corrida()
    assert c.correr() == 0
    mostradas = [l for l in red.llamadas_ia[0]["messages"][1]["content"].splitlines() if l.startswith("id=")]
    assert sum("Binance" in l for l in mostradas) == 6 and any("BCRA" in l for l in mostradas)


def test_promo_en_x_usa_la_primera_oracion_si_no_entra_todo(cfg):
    p = texto.normalizar_promo({"titular": "Un gancho corto", "texto": "Primera oración bien cortita. " + "Relleno largo " * 20,
                                "puntos": ["Uno dos tres", "Cuatro cinco"]})
    ll = {"texto": "Registrate con mi link", "enlace": "https://ejemplo.test/x", "aviso": "Podés perder capital."}
    x = texto.promo_x(p, ll, cfg["x"])
    assert "Primera oración bien cortita." in x and "Relleno" not in x and texto.peso_x(x) <= 280


# ============================== promos con fecha, reintegros, días flojos y links en X

def _solo_promo(c, id_promo: str, **cambios):
    """Deja aprobada una sola promo."""
    for p in c.cfg["promos"]:
        p["activa"] = p["id"] == id_promo
        if p["id"] == id_promo:
            p.update(cambios)
    return c


def test_promo_con_reintegro_puede_decir_el_porcentaje(corrida, red, tmp_path):
    del red.feeds["federalreserve.gov"], red.feeds["news.google.com"]
    assert _solo_promo(corrida(), "binance-tarjeta").correr() == 0
    pie = red.telegram_fotos[0]["caption"]
    assert "✅ 3% de reintegro en USDC" in pie and "31 de diciembre" in pie
    assert '<a href="https://www.binance.com/join?ref=202718448">Registrate en Binance con mi link</a>' in pie
    assert red.x_posts[0]["paid_partnership"] is True and "http" not in red.x_posts[0]["text"]
    memoria = json.loads((tmp_path / "estado.json").read_text(encoding="utf-8"))
    assert memoria["publicadas"][0]["promo_id"] == "binance-tarjeta"


def test_promo_vencida_deja_de_salir_y_avisa(corrida, red, tmp_path):
    del red.feeds["federalreserve.gov"], red.feeds["news.google.com"]
    assert _solo_promo(corrida(), "binance-tarjeta", vence="2026-10-08").correr() == 0      # venció ayer
    memoria = json.loads((tmp_path / "estado.json").read_text(encoding="utf-8"))
    assert [p["tipo"] for p in memoria["publicadas"]] == ["noticia"]                         # salió una noticia, no la promo
    assert "venció el 08/10/2026" in red.telegram_mensajes[-1]["text"]
    avisos = len(red.telegram_mensajes)
    assert _solo_promo(corrida(ahora=AHORA + timedelta(minutes=61)), "binance-tarjeta", vence="2026-10-08").correr() == 0
    assert len(red.telegram_mensajes) == avisos                                              # no repite el aviso cada hora


def test_promo_sale_hasta_el_dia_en_que_vence(corrida, red, tmp_path):
    del red.feeds["federalreserve.gov"], red.feeds["news.google.com"]
    assert _solo_promo(corrida(), "binance-tarjeta", vence="09/10/2026").correr() == 0       # vence hoy (escrito a la argentina)
    memoria = json.loads((tmp_path / "estado.json").read_text(encoding="utf-8"))
    assert [p["tipo"] for p in memoria["publicadas"]] == ["promo"]


def test_fecha_de_vencimiento_mal_escrita_frena_la_promo():
    hoy = AHORA.date()
    assert main.promo_vencida({"vence": "fin de año"}, hoy) is True
    assert main.promo_vencida({"vence": ""}, hoy) is False and main.promo_vencida({}, hoy) is False
    assert main.promo_vencida({"vence": "2026-10-09"}, hoy) is False and main.promo_vencida({"vence": "2026-10-08"}, hoy) is True


def test_dia_flojo_tambien_sale_lo_de_impacto_6(corrida, red, tmp_path):
    _sembrar(tmp_path, 2)                                # a las 15:00 corresponden 5 y van 2: el día viene atrasado
    _solo(red, "Mercado Libre anuncia una inversión récord en Brasil para ampliar sus envíos")
    assert corrida().correr() == 0
    assert "Mercado Libre anuncia una inversión récord" in red.telegram_fotos[0]["caption"]
    assert "lleguen a 6 o más" in red.llamadas_ia[0]["messages"][0]["content"]


def test_dia_normal_no_rellena_con_impacto_6(corrida, red, tmp_path):
    _sembrar(tmp_path, 4)                                # van 4 y corresponden 5: viene bien, no hace falta rellenar
    _solo(red, "Mercado Libre anuncia una inversión récord en Brasil para ampliar sus envíos")
    assert corrida().correr() == 0
    assert red.telegram_fotos == []
    memoria = json.loads((tmp_path / "estado.json").read_text(encoding="utf-8"))
    assert memoria["pendientes"] == []                   # una noticia floja no queda guardada para después


def test_relleno_apagado(corrida, red, tmp_path):
    _sembrar(tmp_path, 2)
    _solo(red, "Mercado Libre anuncia una inversión récord en Brasil para ampliar sus envíos")
    c = corrida()
    c.cfg["ritmo"]["impacto_de_relleno"] = 7             # con 7 (igual que el mínimo) nunca rellena
    assert c.correr() == 0
    assert red.telegram_fotos == []
    assert "lleguen a 7 o más" in red.llamadas_ia[0]["messages"][0]["content"]


def test_una_noticia_fuerte_le_gana_al_relleno(corrida, red, tmp_path):
    _sembrar(tmp_path, 2)
    red.feeds.clear()
    red.feeds["coindesk.com"] = _rss("Medio", [
        {"title": "Mercado Libre anuncia una inversión récord en Brasil para ampliar sus envíos", "fecha": AHORA - timedelta(minutes=5), "desc": "Detalles."},
        {"title": "El BCRA compró USD 150 millones y las reservas superan los USD 40.000 millones", "fecha": AHORA - timedelta(minutes=30),
         "desc": "El Banco Central sumó USD 150 millones en la rueda de hoy. Las reservas brutas cerraron en USD 40.120 millones."},
    ])
    assert corrida().correr() == 0
    assert "BCRA" in red.telegram_fotos[0]["caption"]


def test_links_en_x_solo_en_las_promos(corrida, red, tmp_path):
    assert texto.link_en_x({"poner_links": False}, True) is False and texto.link_en_x({"poner_links": True}, False) is True
    assert texto.link_en_x({"poner_links": "promos"}, True) is True and texto.link_en_x({"poner_links": "promos"}, False) is False
    assert texto.link_en_x({"poner_links": "siempre"}, False) is True and texto.link_en_x({}, True) is False

    _solo(red, "Binance lanza una tarjeta física para pagar con cripto en comercios")
    c = _solo_promo(corrida(), "bots-pionex")
    c.cfg["x"]["poner_links"] = "promos"
    assert c.correr() == 0                                # 15:00: toca la promo
    promo = red.x_posts[0]["text"]
    assert "Copiá mis bots en Pionex con mi link: https://accounts.pionex.com/es/signUp?r=0uCbaWYsxRp" in promo
    assert texto.peso_x(promo) <= 280

    c = _solo_promo(corrida(ahora=AHORA + timedelta(minutes=61)), "bots-pionex")
    c.cfg["x"]["poner_links"] = "promos"
    assert c.correr() == 0                                # una hora después: la noticia de Binance, con llamado pero sin link
    noticia = red.x_posts[1]["text"]
    assert "Abrí tu cuenta en Binance con mi link (el link está en mi perfil)" in noticia and "http" not in noticia


# ============================== lo que encontró la revisión independiente (cada caso quedó como prueba)

def _memoria(tmp_path) -> dict:
    return json.loads((tmp_path / "estado.json").read_text(encoding="utf-8"))


def _sin_la_fed(red):
    del red.feeds["federalreserve.gov"], red.feeds["news.google.com"]      # quedan el BCRA (8) y los ETF (7)


# ---------- links

def test_sin_links_corta_cualquier_cosa_que_parezca_link():
    for crudo in ("Según U.Today y PUMP.FUN", "Lo publicó crypto.news", "Web3.foundation lo confirmó", "Fuente: Bitcoin.com News"):
        limpio = texto.sin_links(crudo)
        assert not texto.parece_link(limpio), crudo
        assert limpio.replace(chr(0x200B), "") == crudo                    # se lee exactamente igual
    assert texto.sin_links("Compró $BTC y $ETH por U$S 100") == "Compró BTC y ETH por U$S 100"
    assert texto.sin_links("EE.UU. y las FF.AA.") == "EE.UU. y las FF.AA."


def test_nombre_de_medio_listo_para_publicar():
    assert texto.fuente_limpia("Investing.com Español") == "Investing Español"
    assert texto.fuente_limpia("Infobae.com") == "Infobae" and texto.fuente_limpia("lanacion.com.ar") == "Lanacion"
    assert texto.fuente_limpia("es.investing.com") == "Investing"
    assert texto.fuente_limpia("Reserva Federal (Fed)") == "Reserva Federal (Fed)"
    for nombre in ("crypto.news", "Bitcoin.com News", "U.Today", "Investing.com Español", "YPF S.A."):
        assert not texto.parece_link(texto.fuente_limpia(nombre)), nombre
    assert texto.fuente_limpia({"raro": 1}) == ""


def test_fuente_de_google_noticias_nunca_sale_como_link(corrida, red):
    red.feeds.clear()
    red.feeds["news.google.com"] = _rss("Google", [{
        "title": "Binance lanza una tarjeta física para pagar con cripto en comercios - Investing.com Español",
        "source": "Investing.com Español", "fecha": AHORA - timedelta(minutes=20), "desc": ""}])
    assert corrida().correr() == 0
    x = red.x_posts[0]["text"]
    assert x.endswith("Fuente: Investing Español") and not texto.parece_link(x)
    assert "Fuente: Investing Español" in red.telegram_fotos[0]["caption"]


# ---------- llamado a registrarse

MALAS = [
    "La SEC demandó a Binance por operar sin licencia", "Arrestan a un ejecutivo de Binance en Nigeria",
    "Binance sufre un ciberataque y pausa los retiros", "Binance se va de Argentina y cierra las cuentas",
    "Denuncian a Exness por no pagar retiros", "Binance congela los retiros de sus usuarios", "Exness pierde su licencia en Chipre",
    "Pionex en bancarrota: los usuarios no pueden retirar", "Binance delists 5 tokens after regulator warning",
    "Condenan al fundador de Binance a 4 meses de cárcel", "Binance despide a 1.000 empleados", "Exness quebró y dejó de pagar",
    "Estafó a miles: el caso que salpica a Binance", "Allanan las oficinas de Binance",
    "Binance pagará USD 4.300 millones para cerrar el caso con EE.UU.", "Binance se declara culpable",
    "Caída masiva de Binance deja sin operar a millones", "Advertencia de la CNV contra Exness",
    "Binance bloquea cuentas de usuarios argentinos", "Binance anuncia el cierre de su filial en Rusia", "Binance exits Russia",
]
BUENAS = [
    "Binance lanza una tarjeta física", "Exness suma nuevos instrumentos", "Binance llega a 300 millones de usuarios",
    "Probé la nueva app de Binance y esto encontré", "Binance abre oficinas en Buenos Aires", "Pionex suma un nuevo bot de rejilla",
    "A fines de octubre Binance habilita los pagos con QR",
]


@pytest.mark.parametrize("titulo", MALAS)
def test_detecta_las_malas_noticias(titulo):
    assert es_mala_noticia(titulo), titulo


@pytest.mark.parametrize("titulo", BUENAS)
def test_no_confunde_las_buenas_con_malas(titulo):
    assert not es_mala_noticia(titulo), titulo


@pytest.mark.parametrize("titulo", [
    "La SEC demandó a Binance por operar sin licencia en Estados Unidos", "Arrestan a dos ejecutivos de Binance en Nigeria",
    "Binance congela los retiros de sus usuarios por una falla", "Binance despide a mil empleados en todo el mundo",
    "Binance anuncia el cierre de su filial en Rusia", "Condenan al fundador de Binance a cuatro meses de cárcel",
])
def test_ninguna_mala_noticia_de_un_socio_lleva_llamado(corrida, red, titulo):
    _solo(red, titulo)                       # la IA de mentira dice que es buena y que va el llamado: frena el programa
    assert corrida().correr() == 0
    assert len(red.telegram_fotos) == 1 and "con mi link" not in red.telegram_fotos[0]["caption"]
    assert "con mi link" not in red.x_posts[0]["text"] and "paid_partnership" not in red.x_posts[0]


def test_si_la_noticia_no_es_buena_para_la_marca_no_hay_llamado(corrida, red):
    _solo(red, "Binance lanza una tarjeta física para pagar con cripto en comercios")
    red.para_la_marca = "neutra"
    assert corrida().correr() == 0
    assert "con mi link" not in red.telegram_fotos[0]["caption"] and "paid_partnership" not in red.x_posts[0]


def test_noticia_que_habla_de_rendimientos_no_lleva_tu_link(corrida, red):
    _solo(red, "Binance lanza un producto de ahorro con 12% anual garantizado",
          "Binance presentó un producto de ahorro en dólares digitales con 12% anual garantizado.")
    assert corrida().correr() == 0
    pie = red.telegram_fotos[0]["caption"]
    assert "12% anual" in pie and "con mi link" not in pie and "paid_partnership" not in red.x_posts[0]


def test_en_x_el_llamado_nunca_sale_sin_su_aviso(cfg):
    llamado = texto.armar_llamado(cfg["socios"]["exness"], cfg)
    assert llamado["aviso"]
    con_llamado = 0
    for largo in (20, 45, 70, 88, 110):
        post = _post(titular="Exness suma depósitos locales en pesos para sus clientes de Argentina", fuente="Finance Magnates",
                     dato_clave=("Los depósitos se acreditan en el mismo día y sin costo adicional para el cliente. " * 2)[:largo].strip())
        x = texto.texto_x(post, False, llamado, cfg["x"])
        assert texto.peso_x(x) <= 280
        assert (llamado["texto"] in x) == (llamado["aviso"] in x), x     # los dos o ninguno
        con_llamado += llamado["texto"] in x
    assert con_llamado >= 3                                               # lo que se sacrifica es el dato, no el aviso


def test_promo_en_x_siempre_lleva_el_aviso(cfg):
    llamado = texto.armar_llamado(cfg["socios"]["exness"], cfg, "Abrí tu cuenta en Exness con mi link y pedí el acceso por privado")
    promo = texto.normalizar_promo({"titular": "Un gancho larguísimo " * 6, "texto": "Texto.", "puntos": ["Uno uno", "Dos dos"]})
    x = texto.promo_x(promo, llamado, {"poner_links": "promos"}, "🤖")
    assert texto.peso_x(x) <= 280 and llamado["aviso"] in x and llamado["enlace"] in x


# ---------- promos

def test_la_segunda_revision_frena_lo_que_la_lista_de_palabras_no_ve(corrida, red, tmp_path):
    _sin_la_fed(red)
    red.promo_sutil = 1                          # promete sin usar ninguna palabra prohibida; la revisión la frena y se corrige
    assert _con_promos(corrida()).correr() == 0
    assert "billetera" not in red.telegram_fotos[0]["caption"] and len(red.revisiones) == 2
    assert _memoria(tmp_path)["publicadas"][0]["tipo"] == "promo"


def test_si_la_segunda_revision_insiste_la_promo_no_sale(corrida, red, tmp_path):
    _sin_la_fed(red)
    red.juez_promete = True
    assert _con_promos(corrida()).correr() == 0
    assert [p["tipo"] for p in _memoria(tmp_path)["publicadas"]] == ["noticia"]
    assert "promesa de ganancia" in red.telegram_mensajes[-1]["text"]


def test_si_la_revision_contesta_cualquier_cosa_se_toma_como_promesa(cfg):
    class Cerebro:
        def __init__(self, respuesta): self.respuesta = respuesta
        def pedir_json(self, *a, **k): return self.respuesta
    assert ia.juzgar_promo(Cerebro({"promete": False, "frase": ""}), "x", "y") == (False, "")
    assert ia.juzgar_promo(Cerebro({"promete": "false"}), "x", "y")[0] is False
    for rara in ({}, {"promete": None}, {"promete": "quizás"}, {"promete": True, "frase": "ganás seguro"}, {"otra": 1}):
        assert ia.juzgar_promo(Cerebro(rara), "x", "y")[0] is True, rara


def test_promo_que_la_ia_no_logra_escribir_no_traba_al_canal(corrida, red, tmp_path):
    _sin_la_fed(red)
    red.alterar["promo"] = lambda s: "perdón, con eso no puedo ayudar"      # ni siquiera devuelve el formato pedido
    assert _con_promos(corrida()).correr() == 0
    memoria = _memoria(tmp_path)
    assert [p["tipo"] for p in memoria["publicadas"]] == ["noticia"]        # en su lugar salió una noticia
    assert list(memoria["promos"]) == ["bots-pionex"]                       # y esa promo pasó al final de la fila
    assert _con_promos(corrida(ahora=AHORA + timedelta(minutes=61))).correr() == 0
    memoria = _memoria(tmp_path)
    assert [p["tipo"] for p in memoria["publicadas"]] == ["noticia", "noticia"]
    assert set(memoria["promos"]) == {"bots-pionex", "pionex-bienvenida"}   # probó con la otra


def test_los_puntos_largos_de_una_promo_entran_en_la_imagen(cfg):
    promo = {"titular_imagen": "Bots que operan por vos", "puntos": ["Un renglón larguísimo que antes se cortaba contra el borde", "Corto"]}
    jpg = imagen.armar_promo(promo, "PROMO", "Registrate en Pionex con mi link", "", cfg, AHORA)
    assert Image.open(io.BytesIO(jpg)).size == (1080, 1080)


# ---------- respuestas raras de la IA

def test_respuestas_raras_al_elegir_no_rompen_la_corrida(corrida, red, tmp_path):
    red.alterar["elegir"] = lambda s: json.dumps({"elegidas": json.loads(s)["elegidas"][0]})      # un objeto en vez de una lista
    assert corrida().correr() == 0 and len(red.telegram_fotos) == 1
    raras = [
        lambda s: '{"elegidas": 3}', lambda s: '{"elegidas": true}', lambda s: '{"elegidas": "ninguna"}', lambda s: '{}',
        lambda s: json.dumps({"elegidas": [dict(json.loads(s)["elegidas"][0], impacto=float("inf"))] if json.loads(s)["elegidas"] else []}),
        lambda s: json.dumps({"elegidas": [dict(e, impacto="altísimo", urgente="false") for e in json.loads(s)["elegidas"]]}),
    ]
    for i, rara in enumerate(raras):
        red.alterar["elegir"] = rara
        assert corrida(ahora=AHORA + timedelta(minutes=61 * (i + 1))).correr() == 0
    assert len(red.telegram_fotos) == 1                                    # nada de eso publicó de más ni rompió nada


def test_urgente_escrito_como_texto():
    assert ia._si(True) and ia._si("true") and ia._si("Sí")
    assert not ia._si("false") and not ia._si(None) and not ia._si(0) and not ia._si(False)


def test_si_la_ia_manda_una_lista_donde_iba_una_frase_no_se_publica_nada_roto():
    post = texto.normalizar_post(
        {"titular": {"texto": "La Fed recortó"}, "resumen": ["a", "b"], "fotos": "Jerome Powell", "tono": 3, "bandera": ["🇺🇸"], "fuente": ["x"]},
        "ECONOMÍA", "Reuters")
    assert post["titular"] == "" and post["resumen"] == "" and post["fotos"] == ["Jerome Powell"]
    assert post["tono"] == "neutral" and post["bandera"] == "" and post["fuente"] == "Reuters" and texto.faltantes(post)
    promo = texto.normalizar_promo({"titular": ["Mis bots"], "texto": 5, "puntos": 3})
    assert promo["titular"] == "" and promo["puntos"] == [] and texto.faltantes_promo(promo)
    assert texto.normalizar_promo({"titular": "Mis bots operan solos", "texto": "x", "puntos": True})["puntos"] == []


def test_la_consigna_no_deja_huecos_sin_completar(corrida, red):
    c = corrida()
    c.cfg["ritmo"].update(impacto_minimo=8, impacto_de_relleno=8)
    assert c.correr() == 0
    consigna = red.llamadas_ia[0]["messages"][0]["content"]
    for hueco in ("PISO", "MINIMO", "URGENTE", "CATEGORIAS", "SOCIOS"):
        assert hueco not in consigna, hueco
    assert "poneles impacto 8" in consigna and "lleguen a 8 o más" in consigna and "impacto 9 o más" in consigna


# ---------- nada se pierde, nada se duplica

def test_si_la_ia_se_cae_al_redactar_no_se_pierde_ninguna_noticia(corrida, red, tmp_path):
    red.alterar["redactar"] = lambda s: "no puedo"
    assert corrida().correr() == 0
    memoria = _memoria(tmp_path)
    assert memoria["publicadas"] == [] and len(memoria["pendientes"]) == 3      # la Fed, el BCRA y los ETF esperan su turno


def test_un_error_inesperado_avisa_y_no_pierde_la_noticia(corrida, red, tmp_path):
    _sin_la_fed(red)
    c = corrida()
    c.cfg["colores"]["fondo"] = "#0B0F1G"            # un color mal escrito hace fallar el dibujo de la imagen
    assert c.correr() == 0
    assert red.telegram_fotos == [] and "error inesperado" in red.telegram_mensajes[-1]["text"]
    assert sorted(p["intentos"] for p in _memoria(tmp_path)["pendientes"]) == [1, 1]     # se prueban una vez más


def test_corte_a_mitad_de_camino_no_se_reintenta(corrida, red, tmp_path):
    _sin_la_fed(red)
    red.telegram_excepcion = requests.ReadTimeout("se cortó esperando la respuesta")      # puede haberse publicado igual
    red.x_estado = 503
    assert corrida().correr() == 0
    memoria = _memoria(tmp_path)
    assert memoria["publicadas"] == []
    assert [p["principal"]["titulo"][:11] for p in memoria["pendientes"]] == ["Bitcoin ETF"]   # la del BCRA no se repite; la otra espera
    assert memoria["pendientes"][0]["intentos"] == 0


def test_si_no_hubo_conexion_se_reintenta_y_sale_una_sola_vez(corrida, red, tmp_path):
    _sin_la_fed(red)
    red.telegram_excepcion = requests.ConnectionError(
        "HTTPSConnectionPool(host='api.telegram.org', port=443): Max retries exceeded (Caused by NewConnectionError('Failed to establish a new connection'))")
    red.x_estado = 503
    assert corrida().correr() == 0
    assert sorted(p["intentos"] for p in _memoria(tmp_path)["pendientes"]) == [0, 1]
    red.telegram_excepcion, red.x_estado = None, 201                                       # una hora después todo anda
    assert corrida(ahora=AHORA + timedelta(minutes=61)).correr() == 0
    assert len(red.telegram_fotos) == 1 and len(red.x_posts) == 1 and "BCRA" in red.telegram_fotos[0]["caption"]


def test_x_cuenta_suspendida_no_se_confunde_con_falta_de_credito():
    def respuesta(estado, cuerpo):
        r = requests.Response()
        r.status_code, r._content, r.encoding = estado, cuerpo.encode(), "utf-8"
        return r
    claves = {"a": "una-clave-larga"}
    suspendida = publicar._error_x(respuesta(403, '{"detail":"Your account is suspended and is not permitted to access this feature."}'), "la publicación", claves)
    assert suspendida.clave == "x_suspendida" and "suspendida" in str(suspendida)
    sin_credito = publicar._error_x(respuesta(402, '{"title":"CreditsDepleted","detail":"Your account does not have any credits."}'), "la publicación", claves)
    assert sin_credito.clave == "x_credito"
    assert publicar._error_x(respuesta(403, '{"detail":"You have reached your spend cap."}'), "la publicación", claves).clave == "x_credito"


def test_memoria_danada_no_publica_a_ciegas(corrida, red, tmp_path):
    (tmp_path / "estado.json").write_text('{"publicadas": [{"fecha": "2026-10-0', encoding="utf-8")      # el archivo quedó cortado
    assert corrida().correr() == 0
    assert red.telegram_fotos == [] and red.x_posts == [] and "memoria" in red.telegram_mensajes[-1]["text"]
    memoria = _memoria(tmp_path)                                           # quedó sana otra vez
    assert memoria["publicadas"] == [] and len(memoria["evaluadas"]) >= 3
    assert corrida(ahora=AHORA + timedelta(minutes=61)).correr() == 0
    assert red.telegram_fotos == []                                        # lo que ya estaba dando vueltas no sale repetido


# ---------- repetidas

def test_la_noticia_de_hoy_no_se_confunde_con_la_de_ayer():
    hoy = Grupo(principal=_n("El BCRA compró USD 80 millones y las reservas superan los USD 40.300 millones"))
    assert not ya_contada(hoy, ["El BCRA compró USD 150 millones y las reservas superan los USD 40.000 millones"])
    assert ya_contada(hoy, ["El BCRA compró USD 80 millones y las reservas pasan los USD 40.300 millones"])
    hackeo = Grupo(principal=_n("Hackean a Bitget y le roban más de USD 350 millones"))
    assert ya_contada(hackeo, ["Hackean a Bitget y le roban USD 351 millones"])            # la misma, redondeada
    baja = Grupo(principal=_n("Bitcoin cae por debajo de los USD 90.000"))
    assert not ya_contada(baja, ["Bitcoin cae por debajo de los USD 100.000"])             # es una novedad: lo decide la IA


def test_lo_publicado_hace_mas_de_dos_dias_no_tapa_lo_nuevo(tmp_path, monkeypatch):
    monkeypatch.setattr(mod_estado, "RAIZ", tmp_path)
    memoria = mod_estado.Estado()
    for dias, titulo in ((5, "El BCRA compró USD 150 millones"), (1, "La Fed bajó la tasa")):
        memoria.agregar_publicada({"fecha": (AHORA - timedelta(days=dias)).isoformat(), "tipo": "noticia", "titular": titulo, "titulos_originales": []})
    assert memoria.titulos_recientes(AHORA) == ["La Fed bajó la tasa"]
    assert memoria.titulares_recientes(AHORA) == ["La Fed bajó la tasa"]


# ---------- manejo del bot

def test_forzar_publica_ahora_pero_respeta_el_tope_del_dia(corrida, red, tmp_path):
    _sin_la_fed(red)
    assert corrida(ahora=_local(3, 7)).correr() == 0 and red.telegram_fotos == []                    # de madrugada lo común espera
    assert corrida(ahora=_local(3, 7), forzar=True).correr() == 0 and len(red.telegram_fotos) == 1   # forzado, sale
    _sembrar(tmp_path, 10)                                                                           # ya van 10 hoy
    assert corrida(forzar=True).correr() == 0 and len(red.telegram_fotos) == 1                       # ni forzando pasa el tope


def test_sin_chat_de_avisos_la_corrida_termina_en_rojo(corrida, red, tmp_path):
    _sin_la_fed(red)
    red.promo_tramposa = 99
    c = _con_promos(corrida())
    c.chat_prueba = ""
    assert c.correr() == 1                       # la promo fue rechazada y no hay a quién avisarle: GitHub manda el mail
    assert red.telegram_mensajes == [] and len(red.telegram_fotos) == 1     # igual publicó la noticia


def test_horas_sin_comillas_en_los_ajustes():
    assert a_hora(630) == a_hora("10:30") and a_hora(1230) == a_hora("20:30") and a_hora("07:00").hour == 7


def test_el_flujo_de_github_cuida_la_memoria():
    flujo = yaml.safe_load((imagen.RAIZ / ".github" / "workflows" / "bot.yml").read_text(encoding="utf-8"))
    pasos = {p.get("name"): p for p in flujo["jobs"]["correr"]["steps"] if p.get("name")}
    assert "ls-remote --exit-code" in pasos["Recuperar la memoria del bot"]["run"]
    assert pasos["Correr el bot"]["id"] == "bot"
    assert "steps.bot.outcome != 'skipped'" in pasos["Guardar la memoria del bot"]["if"]


# ============================== modo solo texto (la imagen la pone Eze a mano)

def _al_canal(red) -> list[dict]:
    return [m for m in red.telegram_mensajes if m["chat_id"] == "@canal_real"]


def _al_privado(red) -> list[dict]:
    return [m for m in red.telegram_mensajes if m["chat_id"] == "555"]


def test_los_ajustes_vienen_en_modo_solo_texto(cfg):
    assert cfg["imagen"]["modo"] == "ninguna" and main.Corrida("prueba", 1, False).sin_imagen


def test_modo_solo_texto_publica_sin_imagen(corrida, red, tmp_path):
    _solo(red, "Binance lanza una tarjeta física para pagar con cripto en comercios")
    assert corrida(imagen="ninguna").correr() == 0
    assert red.telegram_fotos == [] and red.x_subidas == []                       # ninguna imagen, en ningún lado
    canal = _al_canal(red)
    assert len(canal) == 1 and canal[0]["parse_mode"] == "HTML"
    assert "<b>💱 Binance lanza una tarjeta física" in canal[0]["text"]
    assert '<a href="https://www.binance.com/join?ref=202718448">Abrí tu cuenta en Binance con mi link</a>' in canal[0]["text"]
    assert json.loads(canal[0]["link_preview_options"]) == {"is_disabled": True}  # el link no arma un cartel aparte
    x = red.x_posts[0]
    assert "media" not in x and x["paid_partnership"] is True and texto.peso_x(x["text"]) <= 280
    hecho = _memoria(tmp_path)["publicadas"][-1]
    assert hecho["foto"] == "" and hecho["telegram"] == 1 and hecho["x"]
    assert len([l for l in red.llamadas_ia if "Elegís la imagen" in l["messages"][0]["content"]]) == 0   # ni busca foto


def test_en_modo_solo_texto_llega_el_pedido_para_chatgpt(corrida, red):
    assert corrida(imagen="ninguna").correr() == 0                                # sale la de la Fed, de última hora
    assert "ÚLTIMA HORA" in _al_canal(red)[0]["text"]
    privado = _al_privado(red)
    assert len(privado) == 1 and privado[0]["parse_mode"] == "HTML"
    pedido = privado[0]["text"]
    assert "ChatGPT" in pedido and "<code>" in pedido and "https://t.me/canal_real/1" in pedido
    assert '"LA FED BAJÓ LA TASA 25 PUNTOS BÁSICOS"' in pedido and '"ÚLTIMA HORA"' in pedido
    assert "a central bank building at dusk" in pedido and "EZEQUIEL BAGNATO | COMUNIDAD INVERSORES" in pedido
    assert "logo oficial" not in pedido                                           # la Fed no es una marca con foco


def test_el_pedido_para_chatgpt_pide_el_logo_de_la_marca(corrida, red):
    _solo(red, "Binance lanza una tarjeta física para pagar con cripto en comercios")
    assert corrida(imagen="ninguna").correr() == 0
    pedido = _al_privado(red)[-1]["text"]         # (antes llega el aviso de que respondió una sola fuente)
    assert "Que se vea bien el logo oficial de Binance." in pedido and 'un cartel que diga "EXCHANGES"' in pedido


def test_el_pedido_para_chatgpt_se_puede_apagar(corrida, red):
    c = corrida(imagen="ninguna")
    c.cfg["imagen"]["idea_para_chatgpt"] = False
    assert c.correr() == 0
    assert len(_al_canal(red)) == 1 and _al_privado(red) == []


def test_promo_en_modo_solo_texto(corrida, red, tmp_path):
    _sin_la_fed(red)
    assert _con_promos(corrida(imagen="ninguna")).correr() == 0
    canal = _al_canal(red)
    assert len(canal) == 1 and "✅ Sin configurar nada" in canal[0]["text"] and red.telegram_fotos == []
    assert "media" not in red.x_posts[0] and red.x_posts[0]["paid_partnership"] is True
    assert _al_privado(red) == []                # la imagen de una promo se hace una vez y queda guardada: no pide una cada vez
    assert _memoria(tmp_path)["publicadas"][0]["tipo"] == "promo"


def test_promo_con_imagen_propia_sale_tal_cual(corrida, red, tmp_path, monkeypatch):
    _sin_la_fed(red)
    Image.new("RGB", (1600, 1600), (200, 30, 30)).save(tmp_path / "promo.png")   # una imagen hecha a mano
    monkeypatch.setattr(main, "RAIZ", tmp_path)
    c = _solo_promo(corrida(imagen="ninguna"), "bots-pionex", imagen="promo.png")
    assert c.correr() == 0
    assert len(red.telegram_fotos) == 1 and len(red.x_subidas) == 1 and "media" in red.x_posts[0]
    publicada = Image.open(tmp_path / "salida" / "01.jpg")
    assert publicada.size == (1280, 1280) and publicada.getpixel((640, 640))[0] > 180    # la de Eze, sin nada encima


def test_muestra_en_modo_solo_texto(corrida, red, tmp_path):
    assert corrida(modo="prueba", imagen="ninguna").correr() == 0
    assert red.telegram_fotos == [] and red.x_posts == []
    textos = [m["text"] for m in _al_privado(red)]
    assert len(textos) == 3 and "ÚLTIMA HORA" in textos[0] and "así saldría en X" in textos[1]
    assert "ChatGPT" in textos[2] and "https://t.me/" not in textos[2]        # en una prueba no hay publicación para enlazar
    assert not (tmp_path / "estado.json").exists()


def test_solo_texto_con_un_canal_caido_no_duplica(corrida, red, tmp_path):
    _sin_la_fed(red)
    red.telegram_error = {"error_code": 500, "description": "Internal Server Error"}
    red.x_estado = 503
    assert corrida(imagen="ninguna").correr() == 1                # ni publicar ni avisar: la corrida queda en rojo
    assert _memoria(tmp_path)["publicadas"] == [] and len(_memoria(tmp_path)["pendientes"]) == 2
    red.telegram_error, red.x_estado = None, 201
    assert corrida(imagen="ninguna", ahora=AHORA + timedelta(minutes=61)).correr() == 0
    assert len(_al_canal(red)) == 1 and len(red.x_posts) == 1 and "BCRA" in _al_canal(red)[0]["text"]


# ============================== lo que Eze decidió promocionar (oct-2026) y cómo viene cargado

def test_asi_vienen_los_ajustes(cfg):
    from bot.util import socios_activos
    assert list(socios_activos(cfg)) == ["exness", "binance"]                  # Pionex está apagado por ahora
    assert cfg["socios"]["exness"]["enlace"] == "https://one.exnessonelink.com/a/ptf6lzlrc8"
    assert cfg["socios"]["binance"]["enlace"] == "https://www.binance.com/join?ref=202718448"
    assert cfg["x"]["poner_links"] is False and cfg["encendido"] is False and cfg["x"]["premium"] is True
    assert cfg["telegram"]["destino"] == "@comunidad_inversores_anuncios"
    assert cfg["telegram"]["tambien_en"] == ["@Tu_Comunidad_Inversores/150910"]
    c = main.Corrida("prueba", 1, False)
    c.ahora = AHORA
    assert [p["id"] for p in c.promos_activas()] == [
        "senales-exness", "exness-un-clic", "video-senales", "labitconf-2026", "binance-tarjeta-octubre", "binance-tarjeta"]
    hoy = AHORA.date()
    for promo in c.promos_activas():
        assert main.imagen_propia(promo["imagen"]) is not None, promo["id"]      # cada una con su imagen
        if str(promo.get("vence") or "").strip():
            assert main.fecha_de_vencimiento(promo) is not None and not main.promo_vencida(promo, hoy), promo["id"]
        if promo.get("texto_fijo"):
            # el texto de Eze: su versión para X entra entera y sin links
            x = texto.texto_propio_x(promo["texto_x"], promo["texto_fijo"], cfg["x"])
            assert not x.endswith("…") and texto.peso_x(x) <= 280 and "http" not in x, promo["id"]
            continue
        socio = cfg["socios"].get(str(promo.get("socio") or ""))
        assert texto.armar_llamado(socio, cfg, promo["llamado"], str(promo.get("enlace") or "")), promo["id"]
        # en las que escribe la IA no hay cifras de resultados
        assert texto.promesas(promo["datos"], promo["datos"]) == [], (promo["id"], texto.promesas(promo["datos"], promo["datos"]))


def test_socio_apagado_no_se_promociona(corrida, red, tmp_path):
    _solo(red, "Pionex lanza un nuevo bot de rejilla para Binance Smart Chain", fuente="decrypt.co")
    c = _con_promos(corrida())                                   # aunque sus promos digan "activa: true"
    c.cfg["socios"]["pionex"]["activo"] = False
    assert c.promos_activas() == []
    assert detectar_socio("Pionex lanza un nuevo bot", c.cfg) == "" and detectar_socio("Binance lanza algo", c.cfg) == "binance"
    assert c.correr() == 0
    assert not any("q=Pionex" in (p.url or "") for p in red.pedidos)            # ni siquiera busca sus noticias
    assert any("q=Binance" in (p.url or "") for p in red.pedidos)
    consigna = red.llamadas_ia[0]["messages"][0]["content"]
    assert "MARCAS CON FOCO (Exness, Binance)" in consigna
    assert [p["tipo"] for p in _memoria(tmp_path)["publicadas"]] != ["promo"]


def test_promo_de_labitconf_con_el_texto_de_eze(corrida, red, tmp_path):
    _sin_la_fed(red)
    assert _solo_promo(corrida(), "labitconf-2026").correr() == 0
    assert red.llamadas_ia == [] or all("PROMO" not in json.dumps(l) for l in red.llamadas_ia[-1:])
    pie = red.telegram_fotos[0]["caption"]
    assert '👉 <a href="https://www.hallos.io/event/labitconf/checkout">Sacá tu entrada acá</a>' in pie
    assert "COMUNIDADINVERSORES40" in pie and "30 y 31 de octubre" in pie
    x = red.x_posts[0]
    assert "COMUNIDADINVERSORES40" in x["text"] and "http" not in x["text"] and x["paid_partnership"] is True
    assert "media" in x                                                     # con el flyer de Eze
    assert _memoria(tmp_path)["publicadas"][0]["promo_id"] == "labitconf-2026"


def test_promo_de_senales_con_el_texto_de_eze(corrida, red, tmp_path):
    _sin_la_fed(red)
    assert _solo_promo(corrida(), "senales-exness").correr() == 0
    pie = red.telegram_fotos[0]["caption"]
    assert "77,8% cierran en ganancia o en cero" in pie and "no garantizan resultados futuros" in pie
    assert '👉 <a href="https://one.exnessonelink.com/a/ptf6lzlrc8">Registrate en Exness con mi link</a>' in pie
    assert '📎 <a href="https://drive.google.com/file/d/1mzqNl57Hj3rjzvWKR_6IoVOQ7xSbbf_C/view">' in pie
    x = red.x_posts[0]["text"]
    assert "77,8%" in x and "-20,7%" in x and "http" not in x and texto.peso_x(x) <= 280


def test_texto_largo_de_eze_sale_foto_y_despues_el_texto(corrida, red, tmp_path):
    _sin_la_fed(red)
    assert _solo_promo(corrida(), "exness-un-clic").correr() == 0
    assert len(red.telegram_fotos) == 1 and red.telegram_fotos[0].get("caption", "") == ""
    canal = _al_canal(red)
    assert len(canal) == 1 and "One-Click Trading" in canal[0]["text"]
    assert "utm_source" not in canal[0]["text"]                              # se limpia el rastreo de ChatGPT
    assert '<a href="https://one.exnessonelink.com/a/ptf6lzlrc8">Exness — registro</a>' in canal[0]["text"]
    assert len(red.x_posts) == 1 and "media" in red.x_posts[0]


def test_promo_del_video_con_el_texto_de_eze(corrida, red, tmp_path):
    _sin_la_fed(red)
    assert _solo_promo(corrida(), "video-senales").correr() == 0
    pie = red.telegram_fotos[0]["caption"]
    assert "https://youtu.be/WGP222nUgvA" in pie and "https://linktr.ee/comunidadinversores" in pie and "9.300" in pie
    assert "El video completo está en mi perfil" in red.x_posts[0]["text"]


def test_las_cifras_inventadas_no_pueden_salir_en_una_venta_de_la_ia(corrida, red, tmp_path):
    _sin_la_fed(red)
    red.alterar["promo"] = lambda s: json.dumps(dict(json.loads(s), texto="Con la tarjeta, US$1.000 se convirtieron en US$3.217."), ensure_ascii=False)
    assert _solo_promo(corrida(), "binance-tarjeta").correr() == 0
    assert [p["tipo"] for p in _memoria(tmp_path)["publicadas"]] == ["noticia"]      # la venta no salió
    assert "cifras que no están en los datos" in red.telegram_mensajes[-1]["text"]


def test_campana_de_binance_en_dos_pasos(corrida, red, tmp_path):
    _sin_la_fed(red)
    assert _solo_promo(corrida(imagen="ninguna"), "binance-tarjeta").correr() == 0
    pie = red.telegram_fotos[0]["caption"]                                   # con la imagen de la tarjeta
    assert '👉 1) <a href="https://www.binance.com/join?ref=202718448">Registrate en Binance con mi link</a>' in pie
    assert '👉 2) <a href="https://www.binance.com/es-AR/events/binance-physical-card-launch">Participá del lanzamiento de la tarjeta</a>' in pie
    assert '🎥 <a href="https://www.instagram.com/p/Dd77w_stfdZ/">Mirá el video de la tarjeta</a>' in pie
    assert "<i>Promoción de Binance: tiene cupos limitados y condiciones propias.</i>" in pie
    x = red.x_posts[0]["text"]
    assert "Registrate en Binance con mi link y participá del lanzamiento de la tarjeta (los links están en mi perfil)" in x
    assert "⚠️ Promoción de Binance" in x and "http" not in x


def test_texto_propio_links_y_limpieza():
    t = texto.texto_propio_telegram("Hola <yo> & vos [mirá esto](https://a.test/x?utm_source=chatgpt.com&id=3)\n\n\n\nhttps://b.test/y?utm_medium=z")
    assert '<a href="https://a.test/x?id=3">mirá esto</a>' in t and "&lt;yo&gt; &amp; vos" in t
    assert t.endswith("https://b.test/y") and "\n\n\n" not in t
    x = texto.texto_propio_x("", "Primera línea con [link](https://a.test)\nresto", {"texto_sin_link": "el link está en mi perfil"})
    assert x.startswith("Primera línea con link") and "El link está en mi perfil" in x and "http" not in x


def test_si_falla_el_texto_despues_de_la_foto_no_se_repite(red):
    from bot import publicar
    publicar.telegram_foto("t", "@c", b"jpg", "")
    largo = "<b>x</b>" + "a" * 1100
    red.telegram_error = None
    original = publicar.telegram_texto
    def falla(*a, **k):
        raise publicar.ErrorPublicacion("se cortó", reintentar=True)
    publicar.telegram_texto = falla
    try:
        with pytest.raises(publicar.ErrorPublicacion) as e:
            publicar.telegram_publicar("t", "@c", b"jpg", largo)
        assert e.value.incierto and not e.value.reintentar
    finally:
        publicar.telegram_texto = original


def test_el_dato_fijo_de_una_promo_nunca_falta(cfg):
    llamado = texto.armar_llamado({}, cfg, "Sacá tu entrada con 40% OFF", "https://www.hallos.io/event/labitconf/checkout", sin_link="en la web de LABITCONF")
    codigo = "Código: COMUNIDADINVERSORES40"
    sin_codigo = texto.normalizar_promo({"titular": "LABITCONF 2026 con descuento para la comunidad", "texto": "Somos Media Partner. " * 12,
                                         "puntos": ["Dos días de charlas", "En Costa Salguero"]})
    x = texto.promo_x(sin_codigo, llamado, cfg["x"], "🎁", codigo)
    assert codigo in x and texto.peso_x(x) <= 280 and "(en la web de LABITCONF)" in x
    assert f"<b>{codigo}</b>" in texto.promo_telegram(sin_codigo, llamado, "🎁", None, codigo)
    con_codigo = texto.normalizar_promo({"titular": "LABITCONF 2026 con descuento", "texto": "Usá el código COMUNIDADINVERSORES40.",
                                         "puntos": ["Dos días de charlas", "En Costa Salguero"]})
    assert texto.promo_x(con_codigo, llamado, cfg["x"], "🎁", codigo).count("COMUNIDADINVERSORES40") == 1      # no lo repite
    assert texto.promo_telegram(con_codigo, llamado, "🎁", None, codigo).count("COMUNIDADINVERSORES40") == 1


def test_take_profit_no_es_una_promesa():
    assert texto.promesas("Te indica dónde poner el Stop Loss y el Take Profit") == []
    assert texto.promesas("Puro profit todos los meses") == ["profit"]



def test_muestra_de_una_promo_con_texto_propio(corrida, red, tmp_path):
    assert corrida(modo="prueba", promo="labitconf-2026").correr() == 0
    assert [f["chat_id"] for f in red.telegram_fotos] == ["555"] and red.x_posts == []
    assert "COMUNIDADINVERSORES40" in red.telegram_fotos[0]["caption"]


# ============================== X Premium: posts largos

def test_x_premium_publica_el_texto_completo(corrida, red, tmp_path):
    _sin_la_fed(red)
    c = _solo_promo(corrida(), "exness-un-clic")
    c.cfg["x"]["premium"] = True
    assert c.correr() == 0
    x = red.x_posts[0]["text"]
    assert "La idea no es operar más rápido por operar más rápido." in x and "Calendario económico integrado" in x
    assert "http" not in x and "Exness — registro (el link está en mi perfil)" in x
    assert texto.peso_x(x) > 280


def test_x_premium_con_links_los_escribe(cfg):
    html_tg = texto.texto_propio_telegram("Mirá [el video](https://youtu.be/abc) y https://linktr.ee/yo")
    con = texto.x_largo(html_tg, cfg["x"], True)
    assert con == "Mirá el video: https://youtu.be/abc y https://linktr.ee/yo"
    sin = texto.x_largo(html_tg, cfg["x"], False)
    assert "http" not in sin and sin.count("(el link está en mi perfil)") == 2


def test_si_x_no_acepta_el_texto_largo_sale_el_corto(corrida, red, tmp_path):
    _sin_la_fed(red)
    c = _solo_promo(corrida(), "exness-un-clic")
    c.cfg["x"]["premium"] = True
    red.x_rechaza_largos = True
    assert c.correr() == 0
    assert len(red.x_posts) == 1 and texto.peso_x(red.x_posts[0]["text"]) <= 280
    assert "Registrate con mi link (está en mi perfil)" in red.x_posts[0]["text"]


def test_sin_premium_sale_el_corto(corrida, red, tmp_path):
    _sin_la_fed(red)
    c = _solo_promo(corrida(), "exness-un-clic")
    c.cfg["x"]["premium"] = False
    assert c.correr() == 0
    assert texto.peso_x(red.x_posts[0]["text"]) <= 280


# ============================== también en el tema "Noticias" del grupo

def test_separar_destino():
    from bot.publicar import separar_destino
    assert separar_destino("@canal") == {"chat_id": "@canal"}
    assert separar_destino("-1001234567890/45") == {"chat_id": "-1001234567890", "message_thread_id": "45"}
    assert separar_destino("-1001234567890") == {"chat_id": "-1001234567890"}


def test_publica_en_el_canal_y_en_el_tema_del_grupo(corrida, red, tmp_path):
    _sin_la_fed(red)
    c = corrida(imagen="ninguna")
    c.tambien_en = ["-1009999/45"]
    assert c.correr() == 0
    canal = _al_canal(red)
    grupo = [m for m in red.telegram_mensajes if m["chat_id"] == "-1009999"]
    assert len(canal) == 1 and len(grupo) == 1
    assert grupo[0]["message_thread_id"] == "45" and grupo[0]["text"] == canal[0]["text"]
    assert len(red.x_posts) == 1                              # en X sale una sola vez


def test_cada_noticia_lleva_el_link_a_tus_redes(corrida, red, tmp_path):
    _sin_la_fed(red)
    c = corrida(imagen="ninguna")
    c.cfg["x"]["premium"] = True
    assert c.correr() == 0
    tg = _al_canal(red)[0]["text"]
    assert tg.rstrip().endswith('📲 <a href="https://linktr.ee/comunidadinversores">Seguime en todas mis redes</a>')
    x = red.x_posts[0]["text"]
    assert "Seguime en todas mis redes (el link está en mi perfil)" in x and "http" not in x
