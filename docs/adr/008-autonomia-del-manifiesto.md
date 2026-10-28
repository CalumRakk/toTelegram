# 008. Autonomía del Manifiesto, Rehidratación de Datos y Desacoplamiento de Recibos

#### Estado
Aceptado (Actualizado por ADR 011: Recibo Agregado de Directorio)

#### Contexto
Nuestra base de datos SQLite es una herramienta de conveniencia y velocidad, pero no debe ser el único lugar donde viva la verdad. Siguiendo nuestra visión de que "el acceso define la propiedad", cualquier usuario que posea un Snapshot (`.json.xz` o contenedor `.ttape`) debe ser capaz de reconstruir el estado completo del sistema o descargar su información, incluso si pierde su base de datos local o formatea su equipo.

Originalmente, el diseño exigía que un archivo físico individual `.json.xz` viviera permanentemente al lado del archivo original en el disco del usuario como "testigo final". En carpetas con cientos de archivos, esto provocó una severa contaminación del sistema de archivos (*Directory Pollution*).

#### Decisión
El Snapshot se diseña como una **Entidad Autónoma de Red**, desacoplando la semilla de recuperación remota del recibo local:

1. **El Snapshot como Semilla Autónoma en Telegram:**
   * El testigo final y oficial del respaldo es el mensaje publicado en Telegram con el hashtag `#totelegram_manifest`, el cual lleva adjunto el archivo físico de manifiesto (`.json.xz` o contenedor `.ttape`).
   * Este manifiesto contiene toda la información necesaria (IDs de mensajes, canales, offsets lineales, hashes y árbol de catálogo TAR) para que `toTelegram` pueda volver a poblar su base de datos local en cualquier máquina mediante **Rehidratación JIT** o mediante el comando `totelegram index` / `totelegram snapshot import`.
2. **La DB como Caché de Estado:**
   * La base de datos local SQLite se considera una caché persistente y acelerador de consultas de lo que reside en los Snapshots. Si la base de datos se pierde, la verdad se recupera escaneando Telegram.
3. **Desacoplamiento del Recibo Local (ADR-012):**
   * En el disco local del usuario no se generan decenas de sidecars `.json.xz` sueltos. El estado de respaldo local se delega al **Recibo Agregado (`.totelegram.json`)** de cada directorio.
   * El usuario conserva la certeza visual de qué archivos están respaldados sin sufrir desorden en su explorador de archivos.

#### Consecuencias

##### Positivas
* **Máxima Resiliencia:** Si la computadora se destruye o el usuario cambia de sistema operativo, basta con iniciar sesión en Telegram y apuntar al canal para rehidratar el índice local y restaurar cualquier archivo.
* **Carpetas Locales Limpias:** Las carpetas del usuario permanecen ordenadas y legibles para herramientas externas (reproductores, Git, exploradores de fotos).
* **Independencia de Formato:** Las carpetas completas viajan empaquetadas en contenedores `.ttape` (híbrido SQLite + manifiesto) y los archivos individuales en `.json.xz`, garantizando que ambos son semillas autónomas universales.

##### Negativas
* La recuperación de volúmenes directamente desde Telegram sin el manifiesto sellado (#totelegram_manifest) es compleja; la integridad del mensaje de snapshot en Telegram es crítica para la rehidratación automática.
