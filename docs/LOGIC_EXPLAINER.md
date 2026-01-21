# Guía de Estados de Disponibilidad (toTelegram)

Este documento explica cómo **toTelegram** toma decisiones inteligentes para gestionar la redundancia y ahorrar recursos basándose en el análisis del ecosistema.

## La Matriz de Redundancia
| Estado | Contexto de Duplicidad | Resolución |
| :--- | :--- | :--- |
| **FULFILLED** | Local (Mismo chat) | **Ignorar:** Ya existe aquí. |
| **REMOTE_MIRROR** | Global (Espejo íntegro) | **Clonar:** Forward desde fuente única. |
| **REMOTE_PUZZLE** | Global (Fragmentado) | **Unificar:** Recolectar piezas de varios chats. |
| **REMOTE_RESTRICTED** | Fantasma (Inaccesible) | **Upload:** Pregunta si debe crear una fuente propia. |
| **SYSTEM_NEW** | Virgen (Nunca visto) | **Upload:** Primera integración al sistema. |


En Resumen, los Estados se encargan de manejar la duplicidad de una forma más conveniente.

1.  **`FULFILLED`**: Gestiona la duplicidad en el **mismo contexto**. Es el freno final para no repetir tareas en el mismo sitio.
2.  **`REMOTE_MIRROR`**: Gestiona la duplicidad **entre contextos (chats)**. Es el clonador inteligente.
3.  **`REMOTE_PUZZLE`**: Gestiona la duplicidad **dispersa**. Es el sanador del ecosistema que previene la corrupción por fragmentación.

---

## Escenario Real: El archivo "Video_Boda.mp4" (MD5: `abc-123`)

Imagina que este archivo pesa 4GB y requiere **3 fragmentos (chunks)**.

#### CASO 1: El Estreno (Primer contacto)
*   **Situación:** El usuario intenta subir el video por primera vez en la historia del ecosistema hacia el chat "Backup".
*   **Diagnóstico:** `SYSTEM_NEW`.
*   **Estado de Duplicidad:** **Nulo.** No hay registros previos.
*   **Resolución:** Subida física de los 3 chunks.
*   **Resultado:** Se crea el `SourceFile`, el `Job` y 3 `RemotePayloads` vinculados al chat "Backup".

#### CASO 2: El Intento Repetido (Duplicidad Local)
*   **Situación:** Al día siguiente, el usuario olvida que ya lo subió e intenta volver a subir "Video_Boda.mp4" al mismo chat "Backup".
*   **Diagnóstico:** `FULFILLED`.
*   **Estado de Duplicidad:** **Local Absoluta.** El objetivo ya se cumplió en ese contexto.
*   **Resolución:** El sistema dice: "Ya disponible". No hace nada. Ahorro de tiempo: 100%.

#### CASO 3: El Espejo en otro Chat (Duplicidad Global)
*   **Situación:** El usuario ahora quiere subir el mismo video a un nuevo chat llamado "Familia".
*   **Diagnóstico:** `REMOTE_MIRROR`.
*   **Estado de Duplicidad:** **Global Íntegra.** El archivo existe completo en "Backup".
*   **Resolución (Smart Policy):** El sistema pregunta: "¿Quieres que lo clone de 'Backup'?". Si dice que sí, hace `forward` de los 3 mensajes originales al chat "Familia".
*   **Ahorro:** 100% de ancho de banda (reenvío instantáneo).

#### CASO 4: El Puzzle Disperso (Duplicidad Fragmentada)
*   **Situación:** Meses después, por errores de limpieza, el chat "Backup" solo conserva la parte 1, y el chat "Familia" solo conserva las partes 2 y 3. El usuario quiere subirlo a un chat "Copia Seguridad".
*   **Diagnóstico:** `REMOTE_PUZZLE`.
*   **Estado de Duplicidad:** **Sistémica Fragmentada.** Ningún chat lo tiene todo solo, pero la red global sí.
*   **Resolución:** El sistema dice: "He encontrado las piezas repartidas en 2 chats. ¿Quieres que las unifique aquí?". Recolecta las partes de ambos chats hacia el nuevo destino.
*   **Ahorro:** 100% de ancho de banda. Re-unifica la integridad del archivo.

#### CASO 5: La Barrera de Privacidad (Duplicidad Fantasma)
*   **Situación:** Un Perfil B (que usa la misma DB compartida) tiene el archivo físico y quiere subirlo a su propio chat. La DB sabe que el Perfil A lo subió antes, pero el Perfil B no tiene acceso al chat de Perfil A.
*   **Diagnóstico:** `RESTRICTED`.
*   **Estado de Duplicidad:** **Inalcanzable.** Existe el registro, pero no el acceso.
*   **Resolución:** El sistema dice: "Parece que ya está en la red, pero no tengo las llaves. Iniciando subida física propia".
*   **Resultado:** Se crea una nueva "fuente de verdad" accesible para el Perfil B.
