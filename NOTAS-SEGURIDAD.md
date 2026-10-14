# Notas de seguridad (revisión 2026-10-07)

## Corregido
- La API REST del puente (puerto 8080) ahora escucha solo en `127.0.0.1`.
  Antes cualquier dispositivo de la misma red podía enviar mensajes o leer archivos del equipo.

## Riesgos latentes (aceptados por ahora)
1. **Prompt injection:** un mensaje recibido puede contener instrucciones ocultas que Claude
   podría seguir (p. ej. reenviar chats). Mantener `send_message`, `send_file` y
   `send_audio_message` con aprobación manual.
2. **Datos sin cifrar:** `whatsapp-bridge/store/` guarda el historial y la sesión.
   No subirla a Git ni a la nube; quien la copie puede suplantar la cuenta.
3. **Dispositivo vinculado:** se ve en WhatsApp > Dispositivos vinculados; se puede cerrar desde el teléfono.
4. **whatsmeow desactualizado (mar-2025):** si no conecta, `go get -u go.mau.fi/whatsmeow`.
5. **Información sensible enviada a Claude:** al leer chats, su contenido se envía a Claude para analizarlo.
