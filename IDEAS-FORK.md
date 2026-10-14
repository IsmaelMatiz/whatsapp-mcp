# Ideas para el fork

Features que le faltan al proyecto, para implementar después.

## 1. Transcripción de audios

**Problema:** los audios solo aparecen como `[audio - Message ID: … - Chat JID: …]`. La IA no puede escuchar el `.ogg`, así que no sabe qué dice.

**Idea:** nueva herramienta MCP `transcribe_audio(message_id, chat_jid)` que:

1. Llame a `download_media` para obtener el `.ogg`.
2. Lo pase por Whisper (local con `faster-whisper`, o una API) y devuelva el texto.
3. Opcional: guardar la transcripción en la BD (columna nueva en `messages`) para no transcribir dos veces y para que `list_messages` la muestre junto al `[audio]`.

ffmpeg ya está instalado (Whisper lo necesita para decodificar).

## 4. Descarga automática por chat (opcional)

Opción de configuración para descargar automáticamente la media de ciertos chats (p. ej. familia) en cuanto llega, para no depender de que la URL siga viva (las URLs de WhatsApp caducan a las pocas semanas).

## Ya hecho

- **Arreglo del 403 al descargar media** (`whatsapp-bridge/main.go`, `extractDirectPathFromURL`): se le quitaba el query string (`?ccb=…&oh=…&oe=…`, la firma) al direct path. La versión nueva de whatsmeow arma la URL con `directPath + "&hash=…"`, así que sin la firma el CDN devolvía 403.
- **Direct path guardado en la BD**: columna `messages.direct_path` con el `GetDirectPath()` del mensaje. Los mensajes viejos sin ese dato siguen usando `extractDirectPathFromURL` como respaldo.
- **Nombres de archivo estables**: la media se guarda como `<ID del mensaje>.<ext>` (extensión según el mimetype), y los documentos como `<ID>_<nombre original>`, con el nombre limpiado para que no pueda salirse de la carpeta del chat. Los archivos viejos con nombre por fecha se renombran solos la próxima vez que se piden.
- **Estado de la media en `list_messages`**: cada mensaje con media dice si ya está descargado (con su ruta), si se puede pedir con `download_media` o si no se puede descargar por falta de datos. El bridge guarda la ruta en `messages.local_path` y ahora usa un upsert para no perder columnas al resincronizar. `download_media` devuelve el motivo real cuando falla.
- **La IA puede ver las imágenes**: `download_media` devuelve, además de la ruta, la imagen como contenido MCP (reducida con ffmpeg a 1568 px como máximo). Se puede desactivar con `show_image=False`.
