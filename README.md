# Bot de noticias · Ezequiel Bagnato | Comunidad Inversores

Una vez por hora busca las noticias que mueven al mundo financiero y cripto, elige la de más impacto,
la resume en español y la publica sola en Telegram y en X. Hoy publica **solo el texto** (la imagen la pone
Eze a mano); también puede armar él mismo una imagen cuadrada (1:1).
Entre las noticias intercala las promos de las herramientas y campañas de Eze.
Apunta a 10 publicaciones por día: 7 noticias y 3 promos.

## Qué hace en cada corrida

1. **Busca** en unas 40 fuentes (medios de Argentina y del mundo, cripto, brokers, exchanges y organismos oficiales).
2. **Filtra**: saca lo viejo, lo repetido, las notas de servicio, las reseñas y lo que nombre a empresas de `no_nombrar`.
3. **Elige**: una IA puntúa el impacto del 1 al 10. Sale lo que llega a 7 (`impacto_minimo`).
4. **Redacta** el resumen. Controles automáticos: ninguna cifra que no esté en la noticia original (se comparan
   por valor: "1,5" no es "15"), ningún link que no sea el propio, sin consejos de inversión.
5. **Imagen**, según `imagen.modo` en `ajustes.yaml`:
   - `ninguna` (como está hoy): no arma imagen. Publica el texto y le manda a Eze por privado un pedido listo
     para pegar en ChatGPT y hacer la imagen de esa noticia (`idea_para_chatgpt`). En Telegram la imagen se
     agrega editando la publicación; en X, una vez publicado, no se puede agregar.
   - `placa`: arma una imagen 1080×1080 con la marca y la cara de Eze. Sale, en este orden, de: logo oficial
     guardado (`assets/marcas`) o foto real de Wikimedia Commons (licencia libre, con crédito) → imagen hecha
     con IA (opcional, apagado) → placa solo de texto.
6. **Publica** en Telegram y en X, y anota lo publicado para no repetirlo.

Si algo falla (se acabó el crédito de X, una llave vencida, una promo que no pasó los controles, etc.)
avisa por Telegram al chat de prueba (`telegram.chat_prueba`). Si no hay chat cargado, o Telegram no anda,
la corrida queda en rojo y GitHub manda un mail.

## El ritmo del día

- Reparte las publicaciones a lo largo del día para que no salgan todas juntas: como mucho una por hora.
- De 00:00 a 07:00 no publica, salvo una noticia de impacto 9 o más ("última hora").
- Si el día va adelantado, las noticias comunes esperan su turno; las de última hora salen igual
  (hasta 2 por encima del tope).
- Si el día viene flojo (van 2 o más de atraso), también sale lo de impacto 6 (`impacto_de_relleno`).
  Siempre sale primero la más fuerte que haya. Si de verdad no pasa nada, publica menos: no inventa relleno.

## Socios, llamados y promos

- **Socios** (`socios` en `ajustes.yaml`): hoy se promocionan Exness y Binance. Pionex está guardado con
  `activo: false`: no tiene prioridad, ni llamado, ni ventas, y no se buscan sus noticias (se prende con
  `activo: true`). Las noticias de los socios activos tienen prioridad, incluidos productos nuevos y campañas. El llamado a registrarse con el link de Eze va solo cuando la noticia es **buena**
  para la marca (como mucho 2 por día). Si es mala (hackeo, multa, demanda, cierre, despidos...), si la marca
  aparece de pasada, o si la noticia habla de rendimientos, sale como noticia y **sin** llamado. Ante la duda, sin llamado.
  En X, el llamado nunca sale sin su aviso de riesgo.
- **Promos** (`promos`): ventas de herramientas y campañas. Salen después de cada horario de `horarios_promos`,
  nunca dos seguidas, van rotando y la IA escribe un texto distinto cada vez, pero solo con los `datos` cargados.
  Solo salen las que dicen `activa: true`.
- **Control de promesas**: una promo nunca puede prometer ganancias. Hay dos revisiones: una lista de palabras
  y frases (ganancia, rendimiento, asegurado, sin riesgo, ingresos pasivos, duplicar el capital...; solo pasan
  dentro de una advertencia honesta como "ningún bot garantiza ganancias") y una segunda lectura hecha por la IA,
  que frena lo que la lista no ve. Un porcentaje solo pasa si es un dato del producto que figura en sus `datos`
  (un reintegro, un descuento) y nunca como rendimiento. Si el texto no pasa dos veces, esa promo no sale:
  en su lugar va una noticia, la promo pasa al final de la fila y llega un aviso.
- **Campañas con fecha**: con `vence: "2026-12-31"` la promo deja de salir sola al día siguiente y avisa.
- **Datos propios de una promo**: `enlace` (su link, si no es el del socio), `aviso` (una aclaración debajo del
  llamado), `extras` (hasta 3 links más, solo en Telegram), `texto_sin_link` (qué decir en X en lugar del link)
  y `dato_fijo` (algo que nunca puede faltar, como un código de descuento).
- **Texto propio** (`texto_fijo` y `texto_x`): la promo sale con el texto de Eze tal cual, con sus cifras, y la IA no
  lo toca. Si es más largo de lo que entra debajo de una foto, sale la foto y enseguida el texto.
- **Ventas que escribe la IA** (las que tienen `datos`): no dan números de resultados ni prometen ganancias.
- **Llamado en dos pasos** (`paso_2`): 1) registrate con mi link (así queda como referido) y 2) entrá a la campaña.
- **Imagen propia** (`imagen`): el flyer de la promo, en `assets/promos/`. Sale tal cual.
- **Imagen de una promo**: si la promo tiene su propia imagen (`imagen: assets/promos/...`, por ejemplo hecha
  a mano en ChatGPT), en modo `ninguna` se publica tal cual junto con el texto. Lo que diga esa imagen no pasa
  por los controles del bot.
- En X, los posts con link de afiliado o promos se marcan como publicidad (lo exige la regla de X).

## Dónde se cambia cada cosa

Todo lo que se puede ajustar está en [`ajustes.yaml`](ajustes.yaml).

| Archivo | Para qué |
|---|---|
| `bot/fuentes.py` | leer las fuentes |
| `bot/filtro.py` | juntar repetidas, descartar, detectar socios y malas noticias, primer puntaje |
| `bot/ia.py` | elegir, redactar noticias y promos, elegir la imagen (Gemini, Groq, Claude u OpenAI) |
| `bot/texto.py` | armar los textos y controlarlos |
| `bot/imagen.py` | logos guardados, fotos reales, imagen con IA y dibujo de las placas |
| `bot/publicar.py` | Telegram y X |
| `bot/estado.py` | la memoria (se guarda sola en la rama `memoria`) |
| `assets/logo.png` | el logo (la cara de Eze en círculo) |
| `assets/marcas/` | logos oficiales de exchanges, monedas y empresas (ver `LICENCIAS.txt`) |
| `.github/workflows/bot.yml` | el horario: una vez por hora |

Para sumar el logo de otra marca: poner el PNG en `assets/marcas/` y agregar su nombre en `indice.json`.

## Llaves (GitHub → Settings → Secrets and variables → Actions)

| Nombre | De dónde sale | ¿Obligatoria? |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | BotFather, en Telegram | para Telegram |
| `GEMINI_API_KEY` | aistudio.google.com (gratis) | sí (o `GROQ_API_KEY` / `ANTHROPIC_API_KEY` / `OPENAI_API_KEY`) |
| `X_API_KEY`, `X_API_SECRET`, `X_ACCESS_TOKEN`, `X_ACCESS_SECRET` | console.x.com (app con permiso *Read and write*) | para X |

## Probar sin publicar

Pestaña **Actions → Bot de noticias → Run workflow**:

- `chats`: muestra en el registro los chats donde está el bot (para saber el destino y el chat de prueba).
- `prueba`: arma las publicaciones y las manda **solo** al chat de prueba. No publica ni toca la memoria.
  Con `promo` = 1 (o el id de una promo) arma una muestra de promo, aunque esté en borrador.
- `real`: publica (necesita `encendido: true`). Con `forzar` publica ahora aunque no toque por horario,
  pero nunca pasa el tope del día.

En la computadora: `pip install -r requirements.txt pytest && python -m pytest -q` corre las pruebas
sin conectarse a nada.

## La memoria

El bot recuerda qué publicó en `estado.json`, guardado en la rama `memoria`. Si esa memoria no se puede bajar,
la corrida se corta antes de publicar (para no repetir). Si el archivo llega dañado, esa hora no publica:
anota lo que ya estaba dando vueltas, avisa y sigue normalmente desde la hora siguiente.

## Costos

GitHub, Telegram, Wikimedia y la IA de Gemini que redacta: gratis. X cobra por publicación: USD 0,015 sin link
y USD 0,20 con link. Por eso `x.poner_links` tiene tres opciones:

| `poner_links` | Qué hace en X | Costo aproximado con 10 por día |
|---|---|---|
| `false` | nunca pone el link; dice "el link está en mi perfil" | USD 4,50 por mes |
| `promos` | pone el link solo en las 3 promos | USD 21 por mes |
| `siempre` | también en las noticias con llamado (hasta 2 por día) | hasta USD 32 por mes |

En Telegram el link va siempre (es gratis). Las imágenes hechas con IA, si se activan, cuestan unos USD 0,03 cada una.
