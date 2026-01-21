# 004. El Chat como limite de integridad

#### Estado
Aceptado

#### Contexto

Un archivo `CHUNKED` podría teóricamente tener sus partes repartidas en 20 chats diferentes. Depender de piezas distribuidas aumenta el riesgo de pérdida de datos si uno de los chats de origen falla.

#### Decisión

Un Job solo se considera válido (UPLOADED o FULFILLED) si todas sus partes están físicamente en el chat de destino del Job.

Si el sistema detecta que el archivo está repartido en varios chats, la acción obligatoria es la **Unificación por Reenvío** hacia el chat de destino. No se permitirá que un Job marcado como completado dependa de mensajes en chats externos.

#### Consecuencias
1.  **Autonomía del Job:** Cada Job completado es un respaldo independiente y autónomo.
2.  **Autosuficiencia del Chat**: Garantizamos que un archivo guardado sea fácil de recuperar. Evitamos "archivos rotos" que dependen de múltiples fuentes externas. Al terminar un proceso, el chat elegido debe contener todo lo necesario para reconstruir el archivo original por sí mismo.
