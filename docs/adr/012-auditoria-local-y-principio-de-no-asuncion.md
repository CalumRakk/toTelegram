# 012. Auditoría Local O(1), Principio de No Asunción y Testigo Booleano de Snapshots

## Estado
Aceptado

## Contexto
Tras la retirada de los recibos en disco (`.totelegram.json` en ADR 011) y la centralización del estado local en SQLite, surgió la necesidad de definir cómo `totelegram status` y `totelegram prune` deben explorar e interpretar el sistema de archivos local.

Existían tres riesgos arquitectónicos:
1. **Sobreasunción ("Pensar por el usuario"):** Tratar carpetas no respaldadas como si fueran "backups incompletos o pendientes", cuando muchas veces son simples directorios organizativos para subidas individuales (`send`).
2. **Degradación de rendimiento:** Hacer escaneos recursivos descontrolados o parsear/descomprimir snapshots legacy comprimidos (`.json.xz`) en cada ejecución de `status`.
3. **Pérdida de identidad por renombrado:** Perder el rastro de un archivo respaldado si el usuario cambiaba su nombre de carpeta.

## Decisión

Adoptamos cinco reglas fundamentales para el motor de auditoría (`LocalAuditService`):

### 1. Principio de No Asunción (Neutralidad de Directorios)
El sistema **nunca presupone la intención del usuario** sobre una carpeta:
* Si una carpeta no está registrada como cinta TAR (`SourceType.FOLDER`) en SQLite ni tiene un contenedor `.ttape` asociado, su estado es estrictamente **`DIRECTORY` (Directorio)**, con canal `[dim]--[/dim]`.
* No se marca como error, ni como advertencia, ni como "pendiente de backup". Es un estado neutral que reconoce su naturaleza de carpeta. Si el usuario desea auditar su interior, navega hacia ella (`totelegram status ./carpeta/`).

### 2. Exploración Superficial de Primer Nivel (Profundidad 1)
Por defecto, la auditoría opera a **profundidad 1 (no recursiva)**:
* Solo inspecciona los archivos y carpetas hijos directos de la ruta solicitada.
* Garantiza tiempos de respuesta inmediatos ($O(1)$) y evita congelar la terminal en árboles de directorios gigantescos (como `node_modules` o bibliotecas multimedia).

### 3. El Snapshot como Testigo Booleano O(1) (Cero Parseo Legacy)
Para evitar la fragilidad de heurísticas y parsers de esquemas obsoletos:
* En disco local, la presencia de un `.json.xz` o `.ttape` actúa exclusivamente como un **testigo visual booleano** (`path.exists()`).
* **NUNCA se descomprime ni se parsea en `status`**: su presencia reporta directamente `✓ Respaldado (Snapshot local)`.
* La verdad y el contenido del respaldo solo se consultan vía red en Telegram mediante su baliza `#src_<hash>` durante operaciones activas (`send` / JIT Discovery).

### 4. Resolución en Dos Escalones para Archivos Movidos
Para evitar calcular hashes pesados en `status`:
* **Camino 1 (Directo):** Búsqueda por `canonical_path` en SQLite. Si coincide en tamaño y `mtime`, el archivo está verificado.
* **Camino 2 (Movido/Renombrado):** Si el Camino 1 falla, busca por `(filename, size, mtime)`. Si coincide, se actualiza automáticamente el `path_str` en la base de datos sin requerir re-hash ni re-subida.

### 5. Poda Asimétrica Segura (Prune Inmutable en SQLite)
* `totelegram prune` **solo actúa sobre archivos individuales con estado verificado `BACKED_UP`**. Ignora carpetas y archivos modificados.
* Al podar, el archivo se elimina del disco local, pero su registro en SQLite **permanece intacto**, transicionando al estado `REMOTE_ONLY` (`☁ Solo en Telegram`).

## Consecuencias

### Positivas
* **Predecibilidad absoluta:** `status` describe lo que hay sin juzgar ni lanzar alertas falsas.
* **Velocidad instantánea:** Consultas en milisegundos gracias a la precarga en lote y la ausencia de I/O pesado de descompresión.
* **Resiliencia ante movimientos:** Renombrar carpetas no rompe el vínculo con los respaldos previos en Telegram.
* **Seguridad de datos:** Es imposible que `prune` borre accidentalmente carpetas o archivos locales editados.

### Negativas / Compromisos
* Para auditar la totalidad de un árbol profundo, el usuario debe consultar subcarpetas de manera específica o usar una bandera de recursividad explícita (`--recursive`).
