# Tests Reales de toTelegram

Esta carpeta contiene pruebas de integración manuales que operan directamente contra los servidores de **Telegram** utilizando la librería estándar `unittest`.

Su objetivo es verificar las partes más críticas del motor:
- Particionado matemático en bloques continuos.
- Subida física de piezas y publicación de Snapshots.
- Descarga, reensamblado y verificación criptográfica (MD5).
- Creación de Cintas de Datos TAR (`.ttape`) y extracción quirúrgica (`--file`).
- Reanudación inteligente en sitio (`RESUMED_IN_PLACE`).

---

## Aislamiento y Seguridad

Para proteger las credenciales personales y la base de datos de producción:
1. **Workspace Aislado:** Todas las sesiones, bases de datos SQLite y logs de prueba se guardan exclusivamente en `~/.config/toTelegram_test/`.
2. **Cero impacto en producción:** Tu cuenta y base de datos habituales en `~/.config/toTelegram/` no se leen ni se modifican.
3. **Limpieza Automática (Basura Cero):** Cada test registra los mensajes que envía y los elimina automáticamente del chat al terminar (`tearDown`), incluso si el test falla.

---

## Preparación Inicial (Solo una vez)

### Requisitos previos en Telegram:
1. Tener una cuenta de Telegram (puede ser tu cuenta o una secundaria de test).
2. Crear un **Canal o Grupo Privado** exclusivo para pruebas.
3. Asegurarte de que tu cuenta sea **Administrador** en ese chat y tenga activo el permiso **"Eliminar mensajes"**.

### Ejecutar el asistente de configuración:
Ejecuta el script interactivo desde la raíz del proyecto:

```bash
python tests_real/setup_test_env.py

# O

python3 tests_real/setup_test_env.py
```

El script te guiará para:
- Iniciar sesión vía código OTP en la sesión de pruebas.
- Validar el chat de pruebas y comprobar que tengas permisos de borrado.


---

## Ejecución de las Pruebas

Para ejecutar todos los tests reales disponibles:

```bash
python3 -m unittest discover -s tests_real -p "test_real_*.py" -v
```

Para ejecutar un test específico:

```bash
python3 -m unittest tests_real/test_real_file_pipeline.py -v
```

> **Nota:** Si ejecutas los tests sin haber configurado el entorno previamente, `unittest` los saltará automáticamente (`Skipped`) con un aviso explicativo.

---

##  Restablecer o Eliminar el Entorno de Test de Forma Segura

Para cerrar la sesión en Telegram y eliminar los archivos locales:

```bash
python tests_real/setup_test_env.py --clean
```
