# 011. Recibo Al subir archivos

## Estado
Aceptado

## Contexto
Anteriormente, cada archivo subido generaba un archivo sidecar comprimido `.json.xz` en el mismo directorio.
En directorios con decenas o cientos de archivos, esto generaba **Contaminación del Sistema de Archivos**:
1. Duplicaba la cantidad de archivos visibles en el explorador gráfico del usuario.
2. Rompía herramientas multimedia externas (Plex, VLC, gestores de fotos) que intentaban indexar los archivos `.json.xz`.
3. Provocaba colisiones y ambigüedades al subir un mismo archivo a múltiples canales de Telegram.

## Decisión
Hemos decidido **sustituir los sidecars `.json.xz` individuales por un Recibo Agregado por Directorio (`.totelegram.json`)**:

1. **Un solo Ledger por Directorio:** Cada carpeta gestiona un único archivo atómico `.totelegram.json` que registra el estado de respaldo de todos sus archivos.
2. **Soporte Multi-Destino Nativo:** Cada entrada en el recibo puede registrar simultáneamente múltiples destinos de Telegram (`chat_id`), resolviendo el soporte multi-canal sin duplicar archivos en disco.
3. **Escritura Atómica Segura:** El ledger se escribe en un archivo temporal con sufijo aleatorio antes de reemplazar el destino atómicamente a nivel de sistema operativo (`replace`), evitando corrupciones por apagados imprevistos.
4. **Habilitación de Comandos de Auditoría e Higiene:**
   * `totelegram status <directorio>`: Inspecciona el ledger y disco para reportar qué archivos están respaldados y en qué canales.
   * `totelegram prune <directorio>`: Elimina del disco local de forma 100% segura únicamente aquellos archivos cuyo respaldo esté comprobado y sin modificaciones locales.
5. **Autonomía Remota Preservada (ADR-008):** En Telegram se sigue publicando físicamente el mensaje con el snapshot autónomo (`#totelegram_manifest`). Si el usuario borra su `.totelegram.json` local, puede reconstruirlo en cualquier momento desde la base de datos o desde Telegram.

## Consecuencias

### Positivas
* **Carpetas limpias:** 100 archivos locales solo generan 1 archivo ledger en lugar de 100 sidecars sueltos.
* **Compatibilidad Multi-Canal perfecta:** Saber si un archivo está en el Canal A, Canal B o ambos reside en una misma estructura JSON.
* **Flujo Prune/Clean nativo:** El usuario puede liberar gigabytes de su disco duro con un solo comando seguro.

### Negativas
* Los exploradores de archivos sin terminal no muestran un archivo par visual evidente para cada archivo individual a menos que se abra el `.totelegram.json` o se use `totelegram status`.
