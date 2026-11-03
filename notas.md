


- RemotePayload debe guardar de Message solo lo basico, no es necesario todo el JSON enorme.
- verificar si se puede especificar otra ruta para la DB
- error de lock de cuenta al cerrarla forzosamente ¿nodo determinista es la solucion?
- Un detector de velocidad de disco, una heuristica simple quizas nos salve de disco duro lentos.
- hay que especificar mejor que "status ." se basa a un estado local. Existe el "riesgo" de marcar un archivo como no subido cuando en realidad ya esta en los server de tg.
- filtro para send y evitar que suba miles de archivos pequeños.
- ~~Estandarizar que solo se utilice CANAL o grupo privado? ~~
    - el estandar abre la duda ¿forward permitiria enviar archivos a cualquier tipo de chat? No, forward es una replica. Es mejor introducir un comando para compartir que cumpla el objetivo a ciega de la logica de totelegram. **por debatir**
- ~~la logica recibo puede que se haya roto a modificar  assimilate_legacy_snapshot, verificar que ocurre con snapshots legacy~~
- ~~Una cuenta no premiun no debe denunciar por chunk_size, si el siguiente payload es interior a tg_max puede subirlo sin problema, sino que pase al siguiente archivo.~~
- ~~Ordenar la subida en el canal/grupo cuando no es secuencial.~~
- Al importar snapshots se deberia actualizar el caption de cada chunk
- ~~Bug con la purga de archivos locales.~~
- ~~El recibo local no tiene sentido cuando existe la DB.~~

## Legacy
- migrar implica llevar los snapthos  a una version adecuada, y luego esos se migran. Además, hay snapshots tar que no tienen la db .tartape ¿que se hace aqui?
