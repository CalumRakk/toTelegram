**toTelegram**

Script para guarda archivos del PC en Telegram sin importar el tamaño.

**Comandos**

El siguiente comando se usa para subir archivos a Telegram. Si el archivo supera los limites de Telegram (2gb) se divide en diferentes partes. Al finalizar la subida se genera un archivo .yaml

        python toTelegram update myfile.mp4

El archivo .yaml se puede usar para descargar el archivo de Telegram, por ejemplo:

        python toTelegram download myfile.yaml

También se puede comprobar si un archivo ya ha sido subido a Telegram

        python toTelegram check myfile.mp4


**En caso de no tener el archivo .yaml**
se puede descargar el contenido pasando un enlace o una lista de enlaces separado con un espacio:

        python toTelegram download "https://t.me/c/1xxxx64760/27"

Para archivos grandes que han sidos dividos en partes por superer los limites de Telegram, el comando download puede recibir el argumento concatenate para volver a unir las partes en un solo archivo una vez se descarguen.

        python toTelegram download "https://t.me/1xxxx64760/2" "https://t.me/1xxxx64760/3" "https://t.me/1xxxx64760/4" -concatenate

o si ya tienes todos los archivos descargados se puede concatenar asi:

        python toTelegram concatenate myfile.mp4_*

# config.yaml
El primer paso es completar el archivo de configuración.

Con el siguiente comando se valida si las credenciales permiten la conección con Telegram

        python toTelegram test config

Si la conección es valida, para subir


