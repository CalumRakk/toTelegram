### ADR 008: Autonomía del Manifiesto y Rehidratación de Datos

**Estado:** Aceptado

**Contexto:**
Nuestra base de datos SQLite es una herramienta de conveniencia y velocidad, pero no debe ser el único lugar donde viva la verdad. Siguiendo nuestra visión de que "el acceso define la propiedad", cualquier usuario que posea un Snapshot (`.json.xz`) debería ser capaz de recuperar su información, incluso si pierde su base de datos local.

**Decisión:**
El Snapshot se diseñará como una **Entidad Autónoma** capaz de reconstruir el estado del sistema.

1.  **El Snapshot como Semilla:** El archivo de manifiesto contendrá toda la información necesaria (IDs de Telegram, rutas, hashes, offsets) para que `toTelegram` pueda volver a poblar su base de datos local (proceso de Rehidratación).
2.  **La DB como Caché de Estado:** La base de datos local se considerará una caché persistente de lo que hay en los Snapshots. Si un Snapshot entra al ecosistema, la DB debe poder "aprender" de él.
3.  **Verificación de Extremo a Extremo:** El proceso de archivado no se considera exitoso hasta que el Snapshot físico se genera correctamente en el disco del usuario. Él es el testigo final del éxito.

**Consecuencias:**
*   **Positivas:** Máxima resiliencia. El usuario puede mover sus backups entre ordenadores o recuperar todo su ecosistema tras un formateo simplemente escaneando sus archivos `.json.xz`.
*   **Negativas:** El Snapshot es un archivo sensible; si se pierde, la recuperación manual de los volúmenes desde Telegram se vuelve extremadamente difícil (aunque no imposible gracias al formato `tar`).
