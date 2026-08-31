# ADR 001: Modelo de Concurrencia Declarativa y Leases Distribuidos


## Contexto

Anteriormente, el control de concurrencia en `toTelegram` presentaba varios problemas de diseño:
1. **Mecanismos fragmentados:** Coexistían bloqueos de archivos en disco del sistema operativo (`filelock` en `.worktable/locks/`), bloqueos en base de datos (`Claim`) y semáforos en memoria para SQLite.
2. **Bloqueo excesivo del Job:** Se bloqueaba el `Job` completo a nivel de base de datos (`job:<id>`), lo cual impedía que dos terminales con cuentas distintas cooperaran para subir piezas de un mismo archivo grande.
3. **Gestión manual y propensa a fugas:** Los leases se adquirían y liberaban con llamadas imperativas y listas manuales (`_active_leases`), haciendo el código de `uploader.py` frágil ante excepciones.

---

## Las 3 Fuentes de Verdad

Para garantizar la consistencia en SQLite local y PostgreSQL distribuido, se establecen tres reglas inmutables:

| Entidad | Regla de Negocio | Identificador de Recurso |
| :--- | :--- | :--- |
| **Cuenta de Telegram** | **1 Cuenta = 1 Subida activa.** Una cuenta no puede subir más de una pieza a la vez en ningún lugar del sistema. | `account:<telegram_user_id>` |
| **Pieza / Payload** | **1 Pieza = 1 Proceso.** Una pieza física solo puede ser procesada por un nodo/cuenta a la vez. | `payload:<payload_id>` |
| **Job (Contenedor)** | **Colaborativo.** El Job NO se bloquea globalmente. Múltiples cuentas pueden tomar piezas distintas del mismo Job en paralelo. | *No requiere lease exclusivo* |

---

## Decisiones Arquitectónicas Adoptadas

### Decisión 1: La Base de Datos es la Única Fuente de Verdad
* **Qué se hizo:** Se eliminaron por completo los archivos temporales `.lock` del disco.
* **Motivo:** Toda la sincronización (local entre terminales o remota entre máquinas) ocurre exclusivamente en la tabla `Claim`. Esto garantiza paridad total de comportamiento entre SQLite y PostgreSQL.

### Decisión 2: API Declarativa basada en Context Managers
* **Qué se hizo:** El acceso a cuentas y piezas se realiza mediante bloques `with` (`guard_account`, `claim_next_payload`).
* **Motivo:** Garantiza que los leases se adquieran atómicamente y se liberen de forma determinista ante salidas normales, errores no controlados o cancelaciones (`Ctrl+C`).

### Decisión 3: Renovación de Leases Reactiva (Heartbeat en Callback)
* **Qué se hizo:** No se utilizan hilos demonio en segundo plano para renovar leases. La renovación se dispara reactivamente dentro del callback de progreso de Pyrogram con una ventana de tiempo (ej. cada 60 segundos).
* **Motivo:** Si la subida se congela o se corta la conexión, los bytes dejan de fluir $\rightarrow$ no hay callback $\rightarrow$ el lease expira en la DB $\rightarrow$ la pieza queda libre para ser retomada sin dejar bloqueos fantasmas permanentes.

### Decisión 4: Esperas Activas Seguras (Pausas y FloodWait)
* **Qué se hizo:** Durante pausas intencionales (`upload_pause_range`) o esperas impuestas por Telegram (`FloodWait`), la espera no se hace con un `sleep` ciego, sino mediante un bucle de latidos segmentados (`heartbeat.sleep()` o bucle en `patches.py`).
* **Motivo:** Evita que pausas legítimas mayores al TTL del lease provoquen la expiración accidental del reclamo mientras el nodo sigue esperando.

---

## Matriz de Resiliencia ante Fallos

| Escenario de Fallo | Comportamiento del Sistema | Resultado |
| :--- | :--- | :--- |
| **Caída total de red / Socket cerrado** | Pyrogram lanza excepción de red. El Context Manager captura la salida y libera el lease. | No se crea `RemotePayload`. La pieza queda disponible para el siguiente intento. |
| **Cierre forzado del proceso (`SIGKILL`)** | El proceso muere sin ejecutar `finally`. El lease queda en DB con fecha `expires_at`. | Al cabo de 5 minutos (TTL), cualquier worker considera el lease expirado y puede reclamar la pieza. |
| **Colisión al milisegundo (2 cuentas piden la misma pieza)** | La base de datos rechaza la segunda inserción mediante `IntegrityError` o filtro atómico. | El worker perdedor descarta la pieza en conflicto y reclama la siguiente de forma transparente. |
| **FloodWait prolongado (> 5 minutos)** | `save_file_patched` itera en pasos de 10s llamando al progreso. | El lease se renueva periódicamente durante toda la espera. |

---

## Contrato de Uso (Ejemplo Canónico)

```python
coordinator = ConcurrencyCoordinator(db, node_id)

# Bloqueo exclusivo de cuenta
with coordinator.guard_account(account_id):

    # Bucle de trabajo colaborativo
    while True:
        with coordinator.claim_next_payload(job, account_id) as claim:
            if claim is None:
                # Job finalizado o sin piezas libres
                break

            # Transmisión con latidos vinculados
            upload_piece(claim.payload, progress_callback=claim.heartbeat.pulse)

            # Pausa opcional segura
            claim.heartbeat.sleep(pause_seconds)
```
