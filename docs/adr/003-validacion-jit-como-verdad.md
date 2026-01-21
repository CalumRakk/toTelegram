# 003. Validacion jit como verdad

#### Estado
Aceptado

#### Contexto
La base de datos local actúa como un índice de metadatos, pero Telegram es el entorno de almacenamiento real y volátil. Los mensajes pueden ser eliminados o los chats volverse inaccesibles sin que la base de datos sea notificada, lo que podría inducir al sistema a reportar una disponibilidad falsa.

#### Decisión
Adoptamos la **Validación JIT (Just-In-Time)**. Antes de cualquier operación de recuperación, reenvío o marcado como "completado", el sistema **debe** realizar una consulta a la API de Telegram (`get_messages`) para confirmar la existencia y accesibilidad física de los recursos antes de actuar.

#### Consecuencias
1.  **Fiabilidad Total:** El sistema nunca reportará que un archivo está "disponible" si no es capaz de obtener sus mensajes en ese instante.
2.  **Rendimiento:** Requiere llamadas adicionales a la API, las cuales deben optimizarse mediante **Smart Batching** (lotes de 200 IDs) para minimizar la latencia.
