


Script para subir archivos a Telegramsin importar el tamaño.

Si se está quedando sin espacio en su maquina, Telegram puede ser una gran opción para almacenar sus archivos.

---

# Como empezar
Siga los siguientes pasos para comenzar a utilizar este proyecto.

**Instalación**

Github permite descargar este proyecto de varias formas, entre ella como un archivo .zip o usando git.

Una vez descargado este proyecto, hay que instalar todas las dependencias

     pip install -r requirements.txt


**Obtener credenciales**

Para usar este proyecto es necesario tener las credenciales de una aplicación de Telegram. 

Si cuenta con una, las credenciales de una aplicación son los campos `api_id` y `api_hash` en https://my.telegram.org/apps

En caso de no tenerla, puedes crear tu propia aplicación siguiendo los siguientes pasos en: https://core.telegram.org/api/obtaining_api_id


---

## Configurar el proyecto.

Lo primero es rellenar con tus propios datos la información del archivo `config.yaml` ubicado en la carpeta base del proyecto.

Al abrir el archivo se ve asi:
```Yaml
API_HASH: "e0n7bf4d"
API_ID: 1585711
CHAT_ID: "https://t.me/+Fz1aDRT"
```
Lo que se ve es:
- `API_HASH` : es la credencial `api_hash` de una aplicación de Telegram, necesaria para autorizar tu cuenta de Telegram. 
- `API_ID`: es la credencial `api_id` de una aplicación de Telegram, necesaria para autorizar tu cuenta de Telegram.
- `CHAT_ID` : es el lugar de Telegram donde se subirán los archivos. Puede ser un enlace de invitación, username o chat_id. En este ejemplo es un enlace de invitación incompleto.

Eso es todo. Ahora podemos usar los siguientes comandos para subir o descargar archivos en Telegram...