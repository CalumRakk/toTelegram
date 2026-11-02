


- RemotePayload debe guardar de Message solo lo basico, no es necesario todo el JSON enorme.
- la logica recibo puede que se haya roto a modificar  assimilate_legacy_snapshot, verificar que ocurre con snapshots legacy
- error de lock de cuenta al cerrarla forzosamente ¿nodo determinista es la solucion?
- Estandarizar que solo se utilice CANAL o grupo privado?
- ~~Una cuenta no premiun no debe denunciar por chunk_size, si el siguiente payload es interior a tg_max puede subirlo sin problema, sino que pase al siguiente archivo.~~
- Un detector de velocidad de disco, una heuristica simple quizas nos salve de disco duro lentos.
- ~~Ordenar la subida en el canal/grupo cuando no es secuencial.~~
- Al importar snapshots se deberia actualizar el caption de cada chunk
- verificar si se puede especificar otra ruta para la DB
- Bug con la purga de archivos locales.
- El recibo local no tiene sentido cuando existe la DB.

## Legacy
- migrar implica llevar los snapthos  a una version adecuada, y luego esos se migran. Además, hay snapshots tar que no tienen la db .tartape ¿que se hace aqui?
