

Script para almacenar en Telegram archivos sin importar el tamaño.

# Comandos
**update**
Para subir un archivo se usa el comando update.
El comando update puede recibir la ruta de un archivo o carpeta, en este caso sube todos los archivos dentro de la carpeta.  

Ejemplo, el siguiente comando sube el archivo myfile.mp4 a telegram

        python toTelegram update myfile.mp4

Los archivos que superan el limite de Telegram (2 GB) se divide y se sube en partes.
Al finalizar la subida siempre se genera un archivo .yaml, en este caso seria myfile.yaml.
El archivo .yaml se puede usar para descargar los archivos subido de una manera facil

**download**
Para descargar un archivo subido se usa el domando download.
El comando download puede recibir la ruta del archivo .yaml generado 

        python toTelegram download myfile.yaml

Inspirado en los tar incrementales, los backups concatables también usan un archivo .snac para saber cuales archivos requieren backups.
