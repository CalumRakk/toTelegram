**toTelegram**

Script para almacenar en Telegram archivos sin importar el tamaño.

# Comandos

El siguiente comando se usa para subir un archivo a Telegram.

        python toTelegram update myfile.mp4

Si el archivo supera los limites de Telegram (2 GB) se divide en varias partes. Al finalizar la subida siempre se genera un archivo .yaml

El archivo .yaml se puede usar para descargar el archivo subido de una manera más simple.

        python toTelegram download myfile.yaml

El comando «download» también puede recibir uno o una lista de 'enlaces de post de Telegram' para descargar el contenido: 

        python toTelegram download https://t.me/1xxxx64760/102 https://t.me/1xxxx64760/103 https://t.me/1xxxx64760/104

<!-- También se puede comprobar si un archivo ya ha sido subido a Telegram

        python toTelegram check myfile.mp4 -->

# config.yaml
El primer paso es completar el archivo de configuración.

Con el siguiente comando se valida si las credenciales permiten la conección con Telegram

        python toTelegram test config

Si la conección es valida, para subir

