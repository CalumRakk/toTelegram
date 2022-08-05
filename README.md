**toTelegram**

Script para almacenar en Telegram archivos sin importar el tamaño.

# Comandos
El comando update puede recibir la ruta de un archivo o carpeta. Si recibe una carpeta sube todos los archivos dentro de forma individual. exceptos los archivos : 

El siguiente comando se usa para subir un archivo a Telegram.

        python toTelegram update myfile.mp4

Si el archivo supera los limites de Telegram (2 GB) se divide en varias partes. Al finalizar la subida siempre se genera un archivo .yaml

El archivo .yaml se puede usar para descargar el archivo de una manera facil.

        python toTelegram download myfile.yaml

También se puede pasar las partes que componen un archivo como una lista de enlaces de telegram:

        python toTelegram download https://t.me/1xxxx64660/102 https://t.me/1xxxx64660/103 https://t.me/1xxxx64660/104

# Version "0.0.3"
- El nombre del archivo recortado se guarda en las propiedades del fileyaml.
