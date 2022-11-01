


Script para subir archivos a Telegram sin importar el tamaño.

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
- `CHAT_ID` : El ID del Canal,grupo o chat donde desea subir los archivos. En caso de desconocer el ID, el valor de CHAT_ID puede ser un enlace de invitación (como el enlace del ejemplo de arriba) y cuando ejecute el programa se reemplazará el valor por el ID correcto. 

Eso es todo. Ahora podemos usar los siguientes comandos para subir o descargar archivos en Telegram...

# Comandos

# update

## Subir un archivo
Para subir archivos está el comando update. por ejemplo, el siguiente comando sube el video "myvideo.mp4" al canal, grupo o chat de Telegram establecido en `config.yaml`

     python main.py update "myvideo.mp4"

Al finalizar siempre se genera un archivo `.json.xz`. En este caso, el archivo será `myvideo.mp4.json.xz` y por defecto se encuentra en la misma ubicación del archivo que fue subido. Este archivo es un snapshot que contiene metadatos y es útil porque brinda una forma sencilla de volver a descargar el archivo (pero esto se explica más adelante). Tambíen al estar comprimido ocupa poco espacio y se puede guardar en cualquier lugar para tener un registro de los archivos que han sido subido.


## Subir los archivos de una carpeta

Al pasarle la ruta de una carpeta al comando update, cada uno de los archivos dentro de la carpeta se subirán.

     python main.py update "myfolder"

### Argumentos de exclusión
Quizas quiera omitir X o Y archivo a la hora de subir los archivos de una carpeta. Para eso existen los argumentos de exclusión. Por ejemplo, el siguiente comando sube todos los archivos de la carpeta `myfolder` exceptos los que pesan menos de 100 MB

     python main.py update "myfolder" --min-size 100MB

o 

Sube todos los que pesan más de 100MB pero que no superen 1GB de peso.

     python main.py update "myfolder" --min-size 100MB --max-size 1GB

Hay otros argumentos de exclusión como `--exclude-words` para excluir los archivos que contengan en su nombre alguna de las palabras establecidas. Las palabras con espacio van dentro de comillas, como en el siguiente ejemplo

     python main.py update "myfolder" --exclude-words 2022 2013 "14 de febrero"

También se pueden excluir extensiones de archivos

     python main.py update "myfolder" --exclude-ext .jpg .bat

Todos estos argumentos pueden ir combinados si desea una mayor precisión.
