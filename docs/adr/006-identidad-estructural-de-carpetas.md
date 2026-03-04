### ADR 006: Identidad Estructural de Carpetas (Fingerprint)

## Estado

Aceptado (Actualizado por delegación a `tartape`)

## Contexto

El MD5 funciona para archivos individuales, pero calcular el hash de una carpeta completa antes de archivarla es costoso. Necesitábamos una forma rápida y determinista de identificar una carpeta, detectar cambios y validar su integridad entre sesiones.

## Decisión
Hemos delegado la generación y validación de la identidad estructural a la librería **`tartape`**.

1.  **Firma mediante `tartape`**: En lugar de calcular el hash manualmente en `toTelegram`, utilizamos el contrato de identidad de `tartape`. La librería es responsable de iterar la estructura, concatenar metadatos (ruta, tamaño, `mtime`) y generar un hash determinista que sirve como `uuid` lógico de la cinta.
2.  **Validación de Integridad**: Antes de reanudar cualquier proceso, `toTelegram` invoca `tape.verify()`. Si la firma actual de la carpeta difiere de la registrada en el catálogo, `toTelegram` detiene la operación.
3.  **Desacoplamiento**: La lógica de qué constituye una "carpeta alterada" reside ahora en `tartape`, permitiendo que `toTelegram` se enfoque únicamente en la orquestación del backup y la gestión de estados de disponibilidad.

## Consecuencias

### Positivas
*   Identificación inmediata de carpetas.
*   Lógica de cambios centralizada y consistente.
*   Menor riesgo de errores durante la verificación.

### Negativas
*   Dependencia del formato y versión de tartape. Cambios en su mecanismo de hash pueden requerir migraciones en la base de datos de toTelegram.
