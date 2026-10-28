# 010. Máquina de Estados Determinista y Semántica de `--force`

## Estado
Aceptado

## Contexto
Anteriormente, la opción `--force` tenía un comportamiento impredecible: en algunos casos borraba datos sin avisar y en otros era saboteada por la deduplicación JIT de `DiscoveryService`, que reportaba *"ya completado, 0 bytes transferidos"*. Además, reanudar subidas interrumpidas días atrás dejaba los trozos dispersos en el feed de Telegram, rompiendo la contigüidad estética y funcional del canal.

## Decisión
Hemos formalizado el ciclo de vida de la subida como una **Máquina de Estados Determinista de 7 Estados**, dotando a `--force` de una semántica higiénica:

### 1. Ley de la Basura Cero (Fail-Fast)
Si una operación requiere limpiar o reordenar mensajes en Telegram y la cuenta activa carece de permisos de administrador (`can_delete_messages == False`), la operación se **detiene inmediatamente** con `PERMISSION_DENIED_CLEANUP`. El sistema nunca abandonará trozos huérfanos en el chat.

### 2. Reubicación sin Desperdicio (Anti-Waste TOC)
Si se ejecuta `--force` sobre una subida incompleta pero con tamaño de corte compatible, no se vuelven a transferir los bytes desde el disco. El sistema utiliza `client.copy_message` para traer las piezas existentes al fondo del canal en milisegundos (0 bytes de red local) y elimina los mensajes viejos, dejando las partes contiguas y consecutivas.

### 3. Sello Autónomo (`SEAL_HEALED`)
Si todas las partes físicas ($N/N$) ya están en Telegram pero faltaba el snapshot, `--force` nunca destruye las piezas: genera y publica el snapshot faltante sin retransferir datos.

### 4. Árbol de Decisión Formal

```text
Entrada: `totelegram send <archivo>` (al chat destino)
                     │
                     ▼
       [ ¿Existe Snapshot completado en TG? ]
       (Búsqueda JIT: #src_<hash> #totelegram_manifest)
            │                                  │
           SÍ                                  NO
            │                                  │
  ┌─────────┴─────────┐                        │
  │ ¿Usa --force?     │                        │
  │                   │                        │
 SÍ                   NO                       ▼
  │                    │         [ ¿Existe Job previo en BD local? ]
  │                    │                       │
  │            ALREADY_FULFILLED               │
  │            (Detiene: "Ya existe.           │
  │             0 bytes transferidos")         │
  ▼                                            │
CREAR NUEVA COPIA                              │
(Sube copia adicional,                         │
 nuevo Job, nuevo snap)                        │
                                  ┌────────────┴────────────┐
                                 SÍ                         NO
                                  │                          │
                                  │                     INICIAR SUBIDA
                                  │                     LIMPIA (Job nuevo)
                                  ▼
                    [ ¿Están TODOS los trozos (N/N) ]
                    [  físicamente en Telegram?     ]
                                  │
                     ┌────────────┴────────────┐
                    SÍ                         NO (Subida a medias)
                     │                         │
               SEAL_HEALED                     ▼
               (Solo publica el      [ ¿El chunk_size del Job previo ]
                snapshot faltante;   [  es COMPATIBLE con la cuenta? ]
                0 bytes de datos)              │
                                ┌──────────────┴──────────────┐
                               SÍ                             NO (Ej: Era 4GB, hoy es 2GB)
                                │                              │
                     [ ¿El usuario usa --force? ]              │
                                │                              │
                   ┌────────────┴────────────┐                 │
                  SÍ                         NO                │
                   │                          │                │
                   ▼                          ▼                │
     [ ¿Tiene permiso para ]            RESUMED_IN_PLACE       │
     [  eliminar mensajes? ]            (Reanuda subida de     │
           │                            las partes faltantes   │
      ┌────┴────┐                       en donde quedaron)     │
     SÍ         NO                                             │
      │          │                                             │
      │    PERMISSION_DENIED_CLEANUP                           │
      │    (Bloqueo: "Faltan permisos                          │
      │     para reordenar el chat")                           │
      ▼                                                        │
RELOCATED_AND_RESUMED                                          │
1. `copy_message` de partes viejas al fondo.                   │
2. Borra mensajes viejos en Telegram.                          │
3. Sube partes restantes al fondo.                             │
(TOC satisfecho, 0 bytes desperdiciados).                      │
                                                               │
                                ┌──────────────────────────────┘
                                │
                    [ ¿El usuario usa --force? ]
                                │
                   ┌────────────┴────────────┐
                  SÍ                         NO
                   │                          │
                   ▼                          ▼
     [ ¿Tiene permiso para ]            INCOMPATIBLE_CAPACITY
     [  eliminar mensajes? ]            (Bloqueo informativo:
           │                            "Cuenta degradada a 2GB.
      ┌────┴────┐                        Usa --force para reiniciar")
     SÍ         NO
      │          │
      │    PERMISSION_DENIED_CLEANUP
      │    (Bloqueo: "No se pueden borrar
      │     las partes de 4GB viejas")
      ▼
CLEAN_RESTART
1. Borra trozos incompatibles en Telegram.
2. Hard-Delete del Job viejo en SQLite.
3. Inicia nuevo Job con corte de 2 GB.
