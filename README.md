## toTelegram

Este proyecto es una herramienta de CLI en Python diseñada para subir archivos a Telegram sin preocuparse por los límites de tamaño (2GB/4GB). La idea no es solo tener un uploader, sino un gestor de disponibilidad que entiende qué archivos ya están en la "nube" de tus chats para evitar subidas redundantes.


### ¿De qué va esto?
A diferencia de un uploader simple, `toTelegram` usa una base de datos local (SQLite) para trackear los archivos por su MD5. Si intentas subir algo que el sistema ya detectó en otro chat al que tienes acceso, intentará hacer un forward (mirror) o reconstruir el archivo desde piezas sueltas (puzzle) en lugar de gastar ancho de banda volviendo a subir los bytes.


---

### Cómo probarlo

Puedes instalar el paquete directamente desde el repositorio sin necesidad de clonarlo manualmente:


**1. Instala usando pip desde GitHub:**

```
pip install totelegram
```

**2. Crea un perfil:**

Necesitas tu **API_ID** y **API_HASH** de [Telegram](https://my.telegram.org/).

```
totelegram profile create
```

**3. Configura un chat de destino**

```
totelegram config search "Mi Nube Privada" --apply
```

**4. Envía archivos o carpetas:**

```
# Enviar archivos individuales
totelegram send video.mp4

# Archivar una carpeta completa como un backup estructurado
totelegram backup ./mis_fotos
```
