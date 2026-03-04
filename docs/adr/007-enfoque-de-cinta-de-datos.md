# ADR 007: El Enfoque de "Cinta de Datos" para archivar Carpetas

## Estado
Aceptado (Evolucionado a orquestación vía `tartape`)

## Contexto
Subir carpetas complejas (miles de archivos pequeños) a Telegram presenta retos logísticos: saturación de API, pérdida de jerarquía y ruido en la base de datos por el manejo individual de archivos. Necesitábamos transformar estructuras complejas en un flujo continuo y determinista.

## Decisión
Hemos decidido adoptar el **Enfoque de "Cinta de Datos"** utilizando la librería `tartape` como motor de serialización y segmentación.

1.  **Cinta como Abstracción Única:** La carpeta se trata como un flujo continuo (`tar`). `tartape` encapsula la jerarquía, nombres y permisos, asegurando que el contenido sea independiente de `toTelegram`.
2.  **Corte por Volúmenes Delegado:** La división física en volúmenes (`.tar.001`, etc.) y el tamaño de los mismos es gestionado internamente por `tartape`. `toTelegram` simplemente consume el iterador de volúmenes que la librería expone.
3.  **Mapa de Navegación (Catálogo):** La generación del índice detallado de qué archivo vive en qué byte de qué volumen es responsabilidad de `tartape`. `toTelegram` persiste este catálogo en la base de datos para permitir búsquedas y recuperación eficiente.
4.  **Inmutabilidad del Escaneo:** El proceso sigue siendo una "foto fija" en el tiempo T0. La rigidez no es un límite, sino una garantía de fidelidad del backup.
5.  **Autonomía de los Datos:** Al usar el estándar `tar` gestionado por `tartape`, garantizamos que el usuario no queda atrapado en nuestra lógica; puede extraer su backup con cualquier herramienta estándar si une los volúmenes.

## Consecuencias

### Positivas
*   **Limpieza Operativa:** Telegram recibe bloques grandes y optimizados. La lógica de segmentación se ha movido fuera de `toTelegram`, eliminando código crítico propenso a errores en nuestra base.
*   **Portabilidad:** La estructura del backup sigue el estándar `tar`, garantizando que el usuario mantenga el control total sobre sus archivos a largo plazo.
*   **Resiliencia:** Al delegar la lectura del flujo a `tartape`, podemos retomar subidas desde cualquier offset con mayor precisión técnica.
