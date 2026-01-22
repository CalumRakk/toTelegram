# 🚧🔨👷‍♂️

<img src="https://github.com/user-attachments/assets/b1d0be53-8370-4b60-b7c5-c04f7a8bef33" width="200">


## toTelegram

Este proyecto es una herramienta de CLI en Python diseñada para subir archivos a Telegram sin preocuparse por los límites de tamaño (2GB/4GB). La idea no es solo tener un uploader, sino un gestor de disponibilidad que entiende qué archivos ya están en la "nube" de tus chats para evitar subidas redundantes.


### ¿De qué va esto?
A diferencia de un uploader simple, `toTelegram` usa una base de datos local (SQLite) para trackear los archivos por su MD5. Si intentas subir algo que el sistema ya detectó en otro chat al que tienes acceso, intentará hacer un forward (mirror) o reconstruir el archivo desde piezas sueltas (puzzle) en lugar de gastar ancho de banda volviendo a subir los bytes.

---

> [!WARNING]
> Advertencia
El proyecto está en fase de desarrollo. La base de datos y la lógica de los contratos de subida pueden cambiar sin previo aviso, lo que podría invalidar registros previos.


---

### Cómo probarlo (si te atreves)

Puedes instalar el paquete directamente desde el repositorio sin necesidad de clonarlo manualmente:


**1. Instala usando pip desde GitHub:**

```
pip install git+https://github.com/CalumRakk/toTelegram.git
```

**2. Crea un perfil:**

```
totelegram profile create
```

> Ten a mano tu **API_ID** y **API_HASH** de [Telegram](https://my.telegram.org/).

**3. Configura un chat de destino si el asistente no lo hizo:**

```
totelegram config set chat_id <id_o_username>
```

**4. Intenta una subida:**

```
totelegram upload /ruta/al/archivo
```
