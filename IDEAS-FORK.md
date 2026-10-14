# Ideas para el fork

Features que le faltan al proyecto, para implementar después.

## Ya hecho

- **Arreglo del 403 al descargar media** (`whatsapp-bridge/main.go`, `extractDirectPathFromURL`): se le quitaba el query string (`?ccb=…&oh=…&oe=…`, la firma) al direct path. La versión nueva de whatsmeow arma la URL con `directPath + "&hash=…"`, así que sin la firma el CDN devolvía 403.
- **Direct path guardado en la BD**: columna `messages.direct_path` con el `GetDirectPath()` del mensaje. Los mensajes viejos sin ese dato siguen usando `extractDirectPathFromURL` como respaldo.
- **Nombres de archivo estables**: la media se guarda como `<ID del mensaje>.<ext>` (extensión según el mimetype), y los documentos como `<ID>_<nombre original>`, con el nombre limpiado para que no pueda salirse de la carpeta del chat. Los archivos viejos con nombre por fecha se renombran solos la próxima vez que se piden.
- **Estado de la media en `list_messages`**: cada mensaje con media dice si ya está descargado (con su ruta), si se puede pedir con `download_media` o si no se puede descargar por falta de datos. El bridge guarda la ruta en `messages.local_path` y ahora usa un upsert para no perder columnas al resincronizar. `download_media` devuelve el motivo real cuando falla.
- **La IA puede ver las imágenes**: `download_media` devuelve, además de la ruta, la imagen como contenido MCP (reducida con ffmpeg a 1568 px como máximo). Se puede desactivar con `show_image=False`.
- **Transcripción de audios**: herramienta `transcribe_audio(message_id, chat_jid, language?)` con faster-whisper en local (modelo `small` en CPU por defecto; se cambia con `WHISPER_MODEL`, `WHISPER_DEVICE`, `WHISPER_COMPUTE_TYPE`). La transcripción se guarda en `messages.transcription` y `list_messages` la muestra junto al audio. La primera vez descarga el modelo (~460 MB).
- **Descarga automática por chat**: herramientas `set_auto_download(chat_jid, enabled)` y `list_auto_download_chats()`, guardadas en la tabla `auto_download_chats`. El bridge descarga en segundo plano la media nueva de esos chats en cuanto llega (no la del historial).
- **Pies de foto, GIFs y stickers**: se guarda el texto escrito debajo de fotos, videos, GIFs y documentos (antes se perdía). Los GIFs se guardan como tipo `gif` y los stickers como `sticker` (antes se ignoraban). `download_media` devuelve la imagen de los stickers y un cuadro (al segundo 1) de videos y GIFs. Los mensajes del historial se desenvuelven igual que los en vivo (temporales, de una sola vista, documentos con pie de foto).
