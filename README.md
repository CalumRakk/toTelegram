**toTelegram**

Script para almacenar en Telegram archivos sin importar el tamaño.

# Comandos

El siguiente comando se usa para subir un archivo a Telegram.

        python toTelegram update myfile.mp4

Si el archivo supera los limites de Telegram (2 GB) se divide en varias partes. Al finalizar la subida siempre se genera un archivo .yaml

El archivo .yaml se puede usar para descargar el archivo de una manera facil.

        python toTelegram download myfile.yaml

También se puede pasar las partes que componen un archivo como una lista de enlaces de telegram:

        python toTelegram download https://t.me/1xxxx64660/102 https://t.me/1xxxx64660/103 https://t.me/1xxxx64660/104
