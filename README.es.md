# toTelegram

`toTelegram` convierte a Telegram en un sistema de almacenamiento en la nube privado, ordenado y sin límites de tamaño.

Está diseñado para que puedas subir archivos gigantescos o carpetas completas, y tener la certeza de qué cosas ya están respaldadas y recuperar exactamente lo que necesitas en cualquier momento.

---

## ¿Por qué toTelegram?

* **Sube cualquier archivo, sin importar su peso:** Olvídate del límite de 2 GB o 4 GB por archivo de Telegram. Puedes subir vídeos de 20 GB, imágenes de disco o proyectos pesados; cuando los descargues, los recuperarás intactos.
* **Carpetas completas con descarga selectiva:** Respalda carpetas enteras conservando su estructura original. Si en el futuro solo necesitas un documento o una foto específica de esa carpeta, puedes descargarlo de inmediato sin tener que bajar los gigabytes del respaldo completo.
* **Ahorro de tiempo y conexión:** Si se corta internet o cancelas el proceso, la subida se reanuda exactamente donde se quedó. Además, reconoce automáticamente lo que ya has subido para no transferir el mismo archivo dos veces.
* **Duplicación instantánea entre canales:** Si quieres tener una copia de tus respaldos en otro canal o compartirla, se clona en segundos directamente en Telegram, sin gastar los datos de tu conexión ni volver a subir nada.
* **Recupera espacio en tu disco con tranquilidad:** Te ayuda a identificar qué archivos locales ya están 100% seguros en Telegram para que puedas eliminarlos de tu ordenador con la certeza de que podrás descargarlos cuando los vuelvas a necesitar.
---

## Instalación

Requiere **Python 3.11** o superior.

```bash
pip install totelegram
```

---

## Primeros pasos

### 1. Iniciar sesión

Ejecuta el asistente guiado para conectar tu cuenta de Telegram:

```bash
totelegram login
```

El programa te pedirá tu número de teléfono y el código de verificación que recibirás en Telegram.

*(Opcional: Si cuentas con tus propias credenciales `API_ID` y `API_HASH` de [my.telegram.org](https://my.telegram.org), puedes ingresarlas durante el proceso).*

### 2. Comprobar la conexión

Puedes verificar en cualquier momento con qué cuenta estás conectado:

```bash
totelegram status
```

---

## Uso habitual

En todas las operaciones de envío o respaldo, debes indicar el destino con el parámetro `-t` o `--target`. Puedes usar:
* `me` para enviarlo a tus **Mensajes Guardados** (ideal para envíos rápidos y personales).
* El nombre o @alias de un **Canal** (ej. `-t "Mis Respaldos"` o `-t @mi_canal`).
* El identificador numérico del canal (ej. `-t -100123456789`).

---

### ¿`send` o `backup`? Cuándo usar cada uno

| Comando | Ideal para... | Cómo se publica en Telegram |
| :--- | :--- | :--- |
| **`send`** | **Archivos sueltos** (películas, instaladores, PDFs, vídeos grandes). | Cada archivo se publica como un elemento independiente en el chat. |
| **`backup`** | **Carpetas completas** (proyectos, fotos familiares, colecciones de documentos). | Se empaqueta todo en un único respaldo ordenado, sin saturar el canal de mensajes y preservando la jerarquía de carpetas. |

---

### Enviar archivos individuales (`send`)

Sube uno o varios archivos a Telegram. Si el archivo es muy pesado, se dividirá de forma automática para que no tengas problemas con el límite de tamaño.

```bash
# Enviar un archivo grande a tus Mensajes Guardados
totelegram send pelicula.mkv -t me

# Enviar varios archivos a un canal
totelegram send video.mp4 instalador.iso manual.pdf -t "Mi Nube"

# Envia todos los archivos de una carpeta
totelegram send peliculas_1080p/ -t "Mi Nube"
```

> ⚠️ **Atención con las carpetas en `send`:**
> Si le pasas una carpeta a `send`, enviará cada archivo interno por separado. **No uses `send` para carpetas con cientos o miles de archivos pequeños** (como fotos o código), ya que publicará miles de mensajes individuales en tu canal. Para carpetas, usa siempre `backup`.

---

### Respaldar carpetas completas (`backup`)

Diseñado específicamente para carpetas. Convierte toda la estructura en un único paquete optimizado, conservando los nombres, subcarpetas y fechas originales.

```bash
# Respaldar una carpeta completa en un canal
totelegram backup ./mis_fotos -t "Respaldo Familiar"
```

* **Canal limpio:** Aunque tu carpeta contenga 50.000 archivos, no inundará el chat de mensajes; genera solo unos pocos bloques limpios y compactos.
* **Extracción selectiva:** Al respaldar con `backup`, ganas la capacidad de descargar un archivo específico de la carpeta en el futuro sin tener que bajarla toda.
---

### Descargar y restaurar (`download`)

Para recuperar un respaldo, solo necesitas el archivo de manifiesto (`.ttape` o `.json.xz`):

```bash
# Restaurar una carpeta o archivo completo
totelegram download respaldo.ttape -o ./recuperado

# Extracción quirúrgica: descargar solo un archivo dentro de una carpeta respaldada
totelegram download respaldo.ttape --file "fotos/viaje/playa.jpg" -o ./destino
```

---

### Comprobar el estado local (`status`)

Si quieres saber qué archivos de una carpeta ya están respaldados en Telegram y cuáles están pendientes:

```bash
totelegram status ./mis_documentos
```

Muestra una lista con el estado de cada archivo: si ya está en Telegram, si fue modificado localmente o si está pendiente de subida.

---

### Replicar a otro canal (`forward`)

Si ya respaldaste un archivo o carpeta en un canal y quieres tener una copia en otro, puedes clonarlo sin transferir datos desde tu ordenador:

```bash
totelegram forward respaldo.ttape -t "Canal Secundario"
```

---

### Liberar espacio en disco (`prune`)

Elimina de tu equipo aquellos archivos que ya han sido confirmados y respaldados al 100% en Telegram, manteniendo intacto el registro para poder descargarlos cuando los necesites:

```bash
# Ver qué se eliminaría sin borrar nada
totelegram prune ./mis_videos --dry-run

# Liberar espacio confirmando la eliminación
totelegram prune ./mis_videos
```

---

## Opciones útiles

* **Reanudar o forzar subida (`--force`):**
  Si una subida quedó a medias o deseas reiniciar el proceso ignorando el estado previo:
  ```bash
  totelegram send archivo.zip -t me --force
  ```
* **Destino predeterminado por entorno:**
  Puedes definir la variable de entorno `TOTELEGRAM_TARGET` para no tener que escribir `-t` en cada comando:
  ```bash
  export TOTELEGRAM_TARGET="Mis Respaldos"
  totelegram send archivo.iso
  ```
* **Manejo de múltiples cuentas:**
  Puedes cambiar de cuenta activa en cualquier momento:
  ```bash
  totelegram switch
  ```
