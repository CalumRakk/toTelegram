# 013. Exclusividad de Canales y Mensajes Guardados como Destinos de Almacenamiento

## Estado
Aceptado

## Contexto
Originalmente, `toTelegram` admitía cualquier destino de Telegram (grupos básicos, supergrupos, canales y Mensajes Guardados). Sin embargo, el soporte para grupos convencionales introdujo tres problemas fundamentales:

1. **Barrera de permisos en MTProto (Imposibilidad de edición cruzada):** En el protocolo de Telegram, un usuario jamás puede editar el mensaje de otro usuario dentro de un grupo o supergrupo, incluso si ambos son administradores o dueños del chat. Esto impedía implementar migraciones o correcciones de metadatos/captions (`#src_<hash>` o `#totelegram_manifest`) en subidas cooperativas multicuenta.
2. **Contaminación e identidad visual:** En los grupos, cada fragmento `.001`, `.002` muestra el remitente y avatar individual de la cuenta que lo transmitió, transformando el chat en una conversación caótica entre cuentas en lugar de un almacén uniforme.
3. **Casos borde y ruido:** Los grupos básicos (límite de 200 miembros) carecen de capacidades de búsqueda avanzada. Además, en los supergrupos los mensajes de sistema, hilos de discusión (*forum topics*) y restricciones de permisos colectivos introducían fricción y falsos positivos en las búsquedas JIT.

## Decisión
Se restringen los destinos de almacenamiento admitidos a dos únicas opciones:

1. **Canales de difusión (`enums.ChatType.CHANNEL`):**
   * Constituyen el estándar exclusivo para almacenamiento colectivo, respaldos estructurados y colaboración multicuenta.
   * Todos los mensajes son publicados bajo la identidad del Canal, garantizando una estética limpia de *Object Storage*.
   * Cualquier cuenta administradora con permisos puede editar o reorganizar mensajes de cualquier otra cuenta, habilitando migraciones de esquemas y auto-sanación de snapshots.
   * La cuenta activa debe ser **Administradora** con permisos obligatorios de publicación (`can_post_messages`) y eliminación (`can_delete_messages`).

2. **Mensajes Guardados (`me` / `enums.ChatType.PRIVATE` propio):**
   * Se conserva como la opción rápida para transferencias de uso personal exclusivo e inmediato.
   * Todos los permisos se consideran implícitamente concedidos y no requiere configuración previa de canal.

**Se rechaza de forma estricta cualquier grupo, supergrupo o chat privado ajeno a `me`.**

## Consecuencias

### Positivas
* **Capacidad de mantenimiento colectivo:** Una sola cuenta administradora puede actualizar captions, enlaces o metadatos de archivos subidos por otras cuentas en el pasado.
* **Apariencia uniforme:** El canal opera como un Bucket S3 cerrado y limpio.
* **Simplificación del código:** Se elimina la fontanería asociada a grupos básicos, migraciones a supergrupos y hilos de conversación.
* **Búsqueda JIT instantánea:** El historial del canal está reservado exclusivamente para los archivos y manifiestos de toTelegram, sin interferencias de usuarios ni bots.

### Negativas / Compromisos
* No es posible utilizar Grupos con Temas (*Forum Topics*) como carpetas visuales. Esta jerarquía se delega enteramente a los contenedores `.ttape` gestionados por `tartape`.
* Los usuarios que deseen utilizar toTelegram para almacenar archivos de forma persistente deben crear un Canal (público o privado) en lugar de un grupo.
