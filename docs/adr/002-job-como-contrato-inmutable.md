# 002. Job como contrato inmutable

## Estado
Aceptado

## Contexto
En versiones anteriores, la creación del `Job` (la intención de subir un archivo a un chat) estaba oculta en un método estático del modelo. Esto generaba una "caja negra" donde no estaba claro por qué se elegía una estrategia (SINGLE o CHUNKED) y causaba inconsistencias si el entorno del usuario cambiaba (ej: el usuario adquiere Telegram Premium a mitad de una tarea).

## Decisión
Hemos decidido tratar al **`Job` como un contrato inmutable de disponibilidad** una vez creado.

1.  **Identidad Única:** Un Job se define estrictamente por el binomio `(SourceFile, TelegramChat)`.
2.  **Inmutabilidad de Estrategia:** Una vez que un Job se registra como `CHUNKED` o `SINGLE`, esa decisión persiste para siempre, independientemente de si el usuario cambia sus settings o su estatus Premium. Esto garantiza que el "mapa" (Snapshot) generado al final sea coherente con las piezas subidas.
3.  **Resiliencia de Chunks:** Si un Job está `PENDING` y los archivos físicos en disco desaparecen, el sistema debe ser capaz de re-generarlos siguiendo exactamente los parámetros (`tg_max_size`) guardados en el contrato del Job.

## Consecuencias
*   **Positivas:** Seguridad total en la reconstrucción de archivos. Los Snapshots son deterministas. El código del uploader es más fácil de testear al tener métodos de ejecución atómicos.
*   **Negativas:** Si un usuario quiere cambiar la estrategia de un archivo (ej. pasar de CHUNKED a SINGLE tras hacerse Premium), debe borrar el Job actual (y sus mensajes en Telegram) para forzar un nuevo contrato.
