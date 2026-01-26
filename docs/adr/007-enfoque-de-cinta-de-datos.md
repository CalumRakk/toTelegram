# ADR 007: El Enfoque de "Cinta de Datos" para archivar Carpetas

## Estado
Aceptado

## Contexto
Subir una carpeta a Telegram archivo por archivo presenta desafíos logísticos importantes. Si intentamos subir un proyecto con 10,000 archivos pequeños (como una carpeta de dependencias), saturamos la API de Telegram y perdemos la jerarquía de directorios. Además, gestionar la redundancia de cada pequeño archivo individual generaría un ruido innecesario en nuestra base de datos.

Necesitamos una forma de transformar una estructura compleja de carpetas en algo que Telegram entienda bien, respetando la integridad del conjunto y asegurando que el usuario pueda recuperar su información incluso años después, sin depender exclusivamente de esta herramienta.

## Decisión
Hemos decidido tratar el archivamiento de carpetas no como una colección de archivos, sino como una **"Cinta de Datos Continua"** (formato `tar`) para preservar la jerarquía y optimizar la comunicación con la API de Telegram.

1.  **La Carpeta como un Todo:** En lugar de cajas separadas, convertimos toda la carpeta en un flujo único de información. Esto preserva nombres, rutas y permisos de forma nativa.
2.  **Corte por Volúmenes:** Como Telegram tiene límites de tamaño (2GB o 4GB), cortaremos esa "cinta" de forma matemática en volúmenes numerados. Cada volumen es lo que finalmente se sube como un mensaje.
3.  **Mapa de Navegación:** Generaremos un Manifiesto que actúe como un índice detallado. Este índice dirá exactamente en qué parte de la cinta se encuentra cada archivo (por ejemplo: "La tesis empieza en el byte 500 del Volumen 1 y termina en el Volumen 2").
4.  **Prioridad a la Simplicidad:** Para mantener el proceso ágil, no buscaremos archivos duplicados dentro de la carpeta. La prioridad es crear una "foto fija" fiel y rápida de la estructura completa.
5.  **Inmutabilidad Estricta del Escaneo:** El proceso se basa en un inventario estático realizado en el tiempo T0.
    *   **Por qué:** Si permitiéramos añadir archivos durante el proceso, correríamos el riesgo del "Efecto Vacío". Si un usuario mueve un archivo de una subcarpeta aún no procesada a una ya procesada, el sistema lo ignoraría, creando un backup incompleto.
    *   **Seguridad:** La rigidez del escaneo no es una limitación, sino una garantía de que el backup es una representación fiel y completa de lo que el usuario vio al pulsar "Enter".

5.  **Autonomía de los Datos:** Al usar el formato `tar` estándar, garantizamos que el usuario sea el dueño real de su backup. Si une los archivos descargados (`.001`, `.002`...), obtendrá un archivo comprimido que puede abrir con cualquier software común.

## Consecuencias
*   **Positivas:**
    *   **Limpieza Operativa:** Telegram recibe pocos mensajes grandes en lugar de miles de mensajes pequeños.
    *   **Portabilidad:** El backup es universal y no depende de la lógica interna de `toTelegram` para ser extraído en el futuro.
    *   **Resistencia a Fallos:** Si la subida se interrumpe, podemos retomar exactamente en el byte donde nos quedamos, sin repetir trabajo ya hecho.
*   **Negativas:**
    *   **Acceso Selectivo:** Para recuperar un solo archivo de 1KB, el sistema debe procesar el volumen completo que lo contiene (un costo asumible para un sistema de backup).
    *   **Rigidez durante el proceso:** Si la carpeta cambia mientras se está "grabando la cinta", el proceso debe detenerse para evitar una copia inconsistente,  lo cual es un compromiso aceptable para una operación de "congelación".
