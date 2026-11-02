# 011. Recibo Agregado de Directorio (.totelegram.json)

## Estado
Superado (Reemplazado por Base de Datos Local SQLite y Validación JIT)

## Contexto
Anteriormente, cada archivo subido generaba un archivo sidecar comprimido `.json.xz` en el mismo directorio.
En directorios con decenas o cientos de archivos, esto generaba **Contaminación del Sistema de Archivos**:
1. Duplicaba la cantidad de archivos visibles en el explorador gráfico del usuario.
2. Rompía herramientas multimedia externas (Plex, VLC, gestores de fotos) que intentaban indexar los archivos `.json.xz`.
3. Provocaba colisiones y ambigüedades al subir un mismo archivo a múltiples canales de Telegram.

Para resolverlo, se introdujo temporalmente el Recibo Agregado por Directorio (`.totelegram.json`).

## Decisión de Retirada y Evolución a SQLite
Aunque `.totelegram.json` redujo la contaminación de $N$ archivos a 1 solo archivo por directorio, se identificó que mantener recibos en el sistema de archivos generaba:
1. **Split-Brain (Doble fuente de verdad local):** Inconsistencias entre la base de datos relacional SQLite (`Source`, `Job`, `RemoteSnapshot`) y los archivos `.totelegram.json` en disco.
2. **Incompatibilidad con medios de solo lectura:** Fallaba en directorios montados como solo lectura (NAS, medios extraíbles protegidos contra escritura).
3. **Escrituras redundantes:** Requería bloqueos y escrituras temporales atómicas (`.totelegram.tmp_*`) en cada subida de archivo.

**Se sustituye completamente el recibo en disco por la Base de Datos SQLite:**
1. **Zero Directory Pollution Real:** 0 archivos adicionales en las carpetas del usuario. Las carpetas locales quedan 100% limpias.
2. **Consultas indexadas ultrarrápidas ($O(1)$):** Los comandos `status` y `prune` consultan directamente `Source` y `Job` en memoria.
3. **Soporte para carpetas de solo lectura:** Respaldar archivos no requiere permisos de escritura en el directorio de origen.
4. **Respaldo seguro en Telegram (ADR-008 y ADR-003):** La verdad definitiva y recuperable vive en Telegram (`#src_<hash>` y `#totelegram_manifest`).
5. **Snapshots legacy sin parseo:** La presencia de un `.json.xz` o `.ttape` antiguo en disco se usa solo como testigo visual booleano de respaldo local sin descomprimir ni parsear; la verdad del manifiesto se consulta vía JIT en Telegram.
