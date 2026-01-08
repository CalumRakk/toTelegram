# 001. Inmutabilidad de las Credenciales del Perfil

## Estado
Aceptado

## Contexto
Los usuarios intentaban cambiar `API_ID` o `API_HASH` mediante el comando `set` manteniendo la misma sesión (`.session`).
Esto causaba inconsistencias donde la sesión autenticada no coincidía con la aplicación registrada, provocando errores de `AuthKeyInvalid` o cierres de sesión inesperados en Pyrogram.

## Decisión
Hemos decidido mover `API_ID` y `API_HASH` a la lista de `INTERNAL_FIELDS` en la clase `Settings`.
Esto efectivamente hace que la identidad de un perfil sea **inmutable** una vez creado.

## Consecuencias
1. **Positivas:** Se garantiza la integridad de la sesión. Es imposible romper un perfil activo cambiando sus credenciales base.
2. **Negativas:** Si un usuario quiere actualizar sus credenciales, debe obligatoriamente crear un perfil nuevo (`create`) y borrar el anterior.
