# 009. Proyección Dinámica de Coordenadas de Archivos (Cálculo O(1) vs Persistencia de Fragmentos)

## Estado
Aceptado

## Contexto
Anteriormente, para permitir la extracción quirúrgica de archivos individuales desde volúmenes de Telegram, toTelegram calculaba de antemano la posición física de cada archivo en cada volumen y persistía estos fragmentos en la tabla `tapemembergps`.

Este modelo presentaba tres desventajas críticas:
1. **Multiplicación de registros:** Un archivo que cruzaba volúmenes requería múltiples filas en la base de datos (HEAD, BODY, TAIL), generando decenas de miles de filas innecesarias en backups masivos.
2. **Acoplamiento rígido con el Job:** Las coordenadas dependían del tamaño de corte (`tg_max_size`). Si la misma carpeta era procesada bajo otra cuenta o tamaño (ej. 2 GB vs 4 GB Premium), las coordenadas físicas debían duplicarse o recalculaban un nuevo esquema.
3. **Leaky Abstraction:** toTelegram requería nociones internas sobre la anatomía de los contenedores TAR.

## Decisión
Hemos decidido **sustituir la persistencia estática de coordenadas por una Proyección Matemática Dinámica $O(1)$** apoyada en `tartape`:

1. **Coordenada Lineal Única:** La tabla `TapeMember` ahora registra únicamente `start_offset`, que representa el byte absoluto donde inicia el archivo dentro del flujo TAR continuo $[0..N)$. Esta coordenada es una verdad inmutable de la carpeta, independiente de Telegram y del tamaño de volumen.
2. **Eliminación de `TapeMemberGPS`:** Se elimina físicamente la tabla `tapemembergps` de la base de datos (Migración de esquema v7).
3. **Proyección bajo demanda delegada a `tartape`:** La resolución de en qué volúmenes y bytes vive un archivo se calcula en memoria en tiempo de ejecución ($O(1)$) mediante `tartape.calculate_file_slices()`.
4. **Encapsulación del estándar TAR:** toTelegram desconoce el tamaño de las cabeceras USTAR (512 bytes) o alineaciones de bloque; esa responsabilidad recae exclusivamente en `tartape`.

## Consecuencias

### Positivas
* **Escalabilidad masiva en BD:** Reducción de más del 65% en las operaciones de inserción relacional (`INSERT`) al indexar carpetas.
* **Desacoplamiento total:** Un mismo registro `TapeMember` sirve indistintamente para Jobs con volúmenes de 2 GB, 4 GB o cualquier tamaño arbitrario.
* **Resiliencia y limpieza:** Se eliminan cientos de líneas de código asociadas al mantenimiento de la tabla intermedia y claves foráneas.

### Negativas / Compromisos
* Al momento de la extracción o descarga quirúrgica, las partes y offsets deben resolverse al vuelo evaluando la matemática en memoria (aunque el costo de cómputo es de fracciones de microsegundo por archivo).
