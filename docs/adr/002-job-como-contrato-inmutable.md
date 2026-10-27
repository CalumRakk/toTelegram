# 002. Job como contrato inmutable

## Estado
Aceptado (Actualizado)

## Contexto
Anteriormente, la partición dependía de un enum redundante (`SINGLE` o `CHUNKED`) y el tamaño de corte residía dentro de un JSON (`config.tg_max_size`). Un archivo "single" es matemáticamente una partición de una sola pieza ($N=1$), por lo que manejar un enum abría la puerta a estados inconsistentes.

## Decisión
Tratamos al **`Job` como un contrato inmutable de partición**:

1. **Contrato Físico:** El Job fija su regla de corte directamente en la columna relacional `chunk_size`.
2. **Eliminación del enum `Strategy`:** La condición de corte (`is_chunked`) pasa a ser una propiedad calculada ($size > chunk\_size$). Se elimina el JSON `config`.
3. **Resiliencia Determinista:** Si los archivos temporales desaparecen, las piezas se regeneran de forma idéntica usando exclusivamente `chunk_size`.

## Consecuencias
* **Positivas:** Sin estados inconsistentes. Base de datos y snapshots deterministas y más ligeros. Eliminación de JSONs innecesarios.
* **Negativas:** Si se desea cambiar el tamaño de corte de un archivo (ej. de 2 GB a 4 GB tras adquirir Premium), se debe descartar el Job existente para crear uno nuevo.
