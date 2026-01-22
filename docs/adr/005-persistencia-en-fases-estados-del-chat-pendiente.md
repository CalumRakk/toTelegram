# ADR 005: Persistencia en fases y estados de configuración pendiente

## Estado
Aceptado

## Contexto
El proceso de creación de un perfil requiere dos tipos de información con diferentes niveles de "costo":
1. **Identidad (Costo Alto):** Requiere interacción con la API de Telegram, recepción de SMS/OTP y validación de 2FA. Es un proceso sensible a bloqueos y límites de frecuencia (*flood waits*).
2. **Topología/Destino (Costo Bajo):** Definir el `CHAT_ID` de destino. Es una configuración volátil que puede fallar por falta de permisos o cambio de opinión del usuario.

Anteriormente, si el asistente para configurar el chat fallaba o el usuario cancelaba la operación, el perfil completo (incluida la sesión de identidad recién obtenida) se perdía o quedaba en un estado inconsistente (apuntando a "me" por defecto sin consentimiento explícito).

## Decisión
Hemos decidido implementar un **Flujo de Persistencia por Fases** y un **Valor Sentinel** para el destino:

1. **Prioridad de Identidad:** La sesión física (`.session`) y el registro base del perfil (`.env`) se persistirán inmediatamente después de un login exitoso, antes de intentar resolver cualquier configuración de destino.
2. **Valor Sentinel (`NOT_SET`):** Se introduce el valor constante `NOT_SET` para el campo `CHAT_ID`. Este valor indica explícitamente que el perfil tiene una identidad válida pero carece de un destino operativo.
3. **Normalización Tolerante:** La función `normalize_chat_id` aceptará el Sentinel como un estado válido para permitir la carga de configuraciones "en borrador".
4. **Validación en Tiempo de Ejecución:** Los servicios que requieran un destino (como `upload`) deberán validar la presencia de un `CHAT_ID` real y abortar la ejecución con un error instructivo si detectan el Sentinel.

## Consecuencias
*   **Positivas:**
    *   **Resiliencia:** No se pierden logins exitosos si el usuario interrumpe el asistente de configuración.
    *   **Claridad:** El estado del perfil es explícito en los archivos de configuración (`CHAT_ID=NOT_SET`).
    *   **Flexibilidad:** Permite la creación de perfiles mediante automatización para ser configurados posteriormente.
*   **Negativas:**
    *   Añade una pequeña carga de validación al inicio de los comandos operativos para asegurar que el Sentinel no llegue a las capas de red.
