


Script para subir archivos a Telegram sin importar el tamaño.

# Cómo empezar

Lo primero es descargar el código fuente de este proyecto e instalar todas sus dependencias
```shell
$ git clone https://github.com/CalumRakk/toTelegram.git
$ cd toTelegram
$ pip install -r requirements.txt
```

## Configuración
A continuación hay que rellenar con nuestros propios datos el archivo `config.yaml` ubicado dentro del proyecto.

Al abrir el archivo `config.yaml` se ve asi:
```Yaml
API_HASH: e0n7bf4d
API_ID: 1585711
CHAT_ID: https://t.me/+Fz1aDRT
```
Los dos primeros campos (`API_HASH` y `API_ID`) son las credenciales de una aplicación de Telegram y son necesarios para autorizar tu cuenta de Telegram. si ya cuenta con una aplicación de telegram, las credenciales son los campos `api_id` y `api_hash` en https://my.telegram.org/apps

En caso de no tenerla, puedes crear tu propia aplicación siguiendo los siguientes pasos en: https://core.telegram.org/api/obtaining_api_id

El campo `CHAT_ID` es el `ID` en [formato de pyrogram](https://docs.pyrogram.org/topics/advanced-usage#chat-ids) o un `enlace de invitación` o `username` del canal, grupo o chat de Telegram donde desea subir los archivos.


# Comandos
Estos son los comandos basicos para subir archivos:

# update
Para subir archivos está el comando update. por ejemplo, el siguiente comando sube el video "myvideo.mp4" al canal, grupo o chat de Telegram establecido en `config.yaml`

     python main.py update "myvideo.mp4"

El comando update también admite la ruta de una carpeta. En este caso se suben todos los archivos que esten dentro.

     python main.py update "myfolder"

Ten en encuenta que si la ruta de la carpeta tiene espacio se debe pasar entre commilas.

Al finalizar la subida siempre se genera un archivo `.json.xz`. Siguiendo el primer ejemplo, el archivo será `myvideo.mp4.json.xz` y por defecto se encuentra en la misma ubicación del archivo que fue subido. Este archivo es útil porque brinda una forma sencilla de volver a descargar el archivo (pero esto se explica más adelante)


## Argumentos de exclusión
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


# download
Para descargar un archivo en Telegram se le pasa como argumento al comando `download` la ubicación del archivo snapshot: 

     python main.py download "myvideo.mp4.json.xz"

Como se menciono anteriomente, cuando se sube un archivo a telegram se genera un pequeño archivo snapshot con la extension `json.xz` que contiene los identificadores necesarios para encontrar, descargar y concatenar nuevamente el archivo en uno solo.

...

# Errores

## FileNotFoundError
Es necesario añadir la ubicación del binario ffmpeg en las variables del sistema. 

[Tutorial para windows](https://phoenixnap.com/kb/ffmpeg-windows)

