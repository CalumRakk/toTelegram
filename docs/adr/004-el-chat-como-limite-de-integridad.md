# 004. El Chat como límite de integridad y Aislamiento de Destinos

#### Estado
Aceptado (Actualizado por ADR 010: Eliminación de Reenvíos Implícitos)

#### Contexto
Un archivo particionado podría teóricamente tener sus partes repartidas en múltiples chats. Depender de piezas distribuidas aumenta exponencialmente el riesgo de pérdida de datos si uno de los chats de origen se vuelve inaccesible o es eliminado.

Originalmente, se contempló una "Unificación por Reenvío" automática (Smart Forward implícito) donde el uploader buscaba silenciosamente en todos los canales del usuario para evitar subir datos. Este comportamiento violaba el principio de menor sorpresa (*Principle of Least Astonishment*), generaba acoplamiento oculto entre canales ajenos y causaba efectos secundarios no solicitados.

#### Decisión
El **Chat es el Límite Estricto y Absoluto de Integridad**:

1. **Autosuficiencia Obligatoria:** Un `Job` solo es válido si **todas** sus partes físicas ($N/N$) y su `RemoteSnapshot` residen dentro del chat destino del `Job`. Al terminar un proceso, el chat elegido debe contener todo lo necesario para reconstruir el archivo original por sí mismo.
2. **Aislamiento en Subida (`send` / `backup`):** Las operaciones de subida **nunca** buscarán piezas en otros chats ni realizarán reenvíos implícitos. Su evaluación de disponibilidad es estrictamente local al chat de destino configurado.
3. **Replicación Explícita (`forward`):** La replicación de un respaldo hacia otro chat se desacopla por completo del flujo de subida y se promueve a un comando independiente de primera clase (`totelegram forward`). Solo el usuario decide cuándo y hacia dónde duplicar datos entre chats.

#### Consecuencias

##### Positivas
* **Autonomía Total del Chat:** Cada canal es un almacén independiente y cerrado. No existen dependencias fantasma entre chats de trabajo, personales o grupos.
* **Predecibilidad:** `totelegram send` solo interactúa con el chat indicado en la orden, eliminando transferencias invisibles entre canales.
* **Simplicidad en el Motor:** Se eliminan cientos de líneas de código asociadas al rastreo multi-chat (`StrategyResolver`, `SmartForwardStrategy`, `FulfilledStrategy`).

##### Negativas
* Si un archivo idéntico ya fue subido a otro canal del mismo usuario, `totelegram send` no lo reutilizará automáticamente; el usuario debe invocar conscientemente `totelegram forward` si desea replicarlo sin transferir bytes.
