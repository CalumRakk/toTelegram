### ADR 006: Identidad Estructural de Carpetas (Fingerprint)

**Estado:** Aceptado

**Contexto:**
Para archivos individuales, usamos el MD5 como ancla de identidad. Sin embargo, calcular el MD5 de una carpeta de 500GB antes de empezar a archivar sería ineficiente y frustrante para el usuario. Necesitamos una forma de dar "nombre y cara" a una carpeta de forma instantánea, permitiéndonos saber si ha cambiado o si es la misma que ya empezamos a procesar en otra sesión.

**Decisión:**
Usaremos una **Firma Estructural** basada en metadatos para identificar carpetas en el tiempo T0.

1.  **Composición de la Firma:** La identidad de la carpeta se genera concatenando la ruta relativa, el tamaño y la fecha de modificación (`mtime`) de todos los archivos encontrados en el escaneo inicial.
2.  **Hash de Identidad:** El resultado de esa cadena se procesa mediante un algoritmo de hash rápido. Este valor será el `uuid` lógico de la carpeta en nuestra base de datos.
3.  **Detección de Cambios:** Antes de reanudar cualquier proceso o realizar una verificación, el sistema recalculará esta firma. Si el valor difiere del original, el sistema asumirá que la carpeta ha sido alterada y detendrá la operación para proteger la integridad del backup.

**Consecuencias:**
*   **Positivas:** El inicio de cualquier operación es instantáneo. No hay que esperar minutos u horas para que el programa "reconozca" la carpeta.
*   **Negativas:** Un cambio irrelevante (como renombrar un archivo manteniendo su contenido) cambiará la identidad de la carpeta para el sistema. Es un compromiso necesario en favor de la velocidad.
