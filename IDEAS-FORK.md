# Ideas para el fork

Features que le faltan al proyecto, para implementar después.

## 1. Transcripción de audios

**Problema:** los audios solo aparecen como `[audio - Message ID: … - Chat JID: …]`. La IA no puede escuchar el `.ogg`, así que no sabe qué dice.

**Idea:** nueva herramienta MCP `transcribe_audio(message_id, chat_jid)` que:

1. Llame a `download_media` para obtener el `.ogg`.
2. Lo pase por Whisper (local con `faster-whisper`, o una API) y devuelva el texto.
3. Opcional: guardar la transcripción en la BD (columna nueva en `messages`) para no transcribir dos veces y para que `list_messages` la muestre junto al `[audio]`.

ffmpeg ya está instalado (Whisper lo necesita para decodificar).

## 2. Que la IA pueda ver las imágenes

**Problema:** `download_media` solo devuelve la **ruta** del archivo, no la imagen. La IA no la ve, salvo que tenga otra herramienta para leer archivos del disco.

**Idea:** que `download_media` (o una herramienta nueva `get_image`) devuelva la imagen como contenido de imagen MCP (`Image` de FastMCP), además de la ruta local.

## 3. Hacer más evidente que la media se descarga bajo demanda

**Problema:** el bridge solo guarda la metadata (URL cifrada, claves, tamaño). Nada se descarga solo, y la IA no siempre entiende que tiene que llamar a `download_media`.

**Ideas:**

- Que `list_messages` diga explícitamente "usa `download_media` con este ID para obtener el archivo".
- Mostrar si el archivo ya está descargado y su ruta local.
- Indicar cuándo un mensaje **no** se puede descargar por falta de datos (hay ~90 imágenes y ~13 videos así, probablemente del historial sincronizado).

## 4. Descarga automática por chat (opcional)

Opción de configuración para descargar automáticamente la media de ciertos chats (p. ej. familia) en cuanto llega, para no depender de que la URL siga viva (las URLs de WhatsApp caducan a las pocas semanas).

## 5. Nombres de archivo estables

El nombre del archivo se genera con `time.Now()` al **recibir** el mensaje (`audio_20261007_122248.ogg`), no con el ID del mensaje. Usar el ID del mensaje en el nombre evitaría choques y haría el archivo fácil de relacionar con su mensaje.

## Ya hecho

- **Arreglo del 403 al descargar media** (`whatsapp-bridge/main.go`, `extractDirectPathFromURL`): se le quitaba el query string (`?ccb=…&oh=…&oe=…`, la firma) al direct path. La versión nueva de whatsmeow arma la URL con `directPath + "&hash=…"`, así que sin la firma el CDN devolvía 403.
- **Direct path guardado en la BD**: columna `messages.direct_path` con el `GetDirectPath()` del mensaje. Los mensajes viejos sin ese dato siguen usando `extractDirectPathFromURL` como respaldo.
