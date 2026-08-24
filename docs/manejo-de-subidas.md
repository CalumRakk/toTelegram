## ¿Cómo maneja toTelegram la subida simultánea de archivos?

Para mantener el programa rápido, simple y confiable al usar una base de datos compartida (como PostgreSQL), el control de lo que se está subiendo funciona bajo estas reglas sencillas:

#### 1. En la misma computadora
Si abres varias terminales en la misma máquina para subir un archivo grande en partes, el programa se coordina de forma automática para que cada proceso suba una pieza distinta sin pisarse ni repetir trabajo.

#### 2. Entre computadoras diferentes (Base de datos compartida)
Si conectas dos o más computadoras a la misma base de datos:
* **El sistema evita choques:** Si la *Computadora A* ya empezó a subir un archivo a un chat, la *Computadora B* detectará que esa tarea ya está en marcha y simplemente no la iniciará.
* **Cada máquina sube sus propios archivos:** No intentamos repartir los pedazos de un mismo archivo entre computadoras distintas por internet. Hacer eso volvería el programa innecesariamente frágil y lento. Cada computadora se encarga de subir sus archivos de principio a fin.
* **Control de cuentas:** El sistema también evita que dos computadoras usen la misma cuenta de Telegram al mismo tiempo para no saturarla ni provocar bloqueos por parte de Telegram.

#### Resumen para el usuario
El programa está diseñado para protegerte de subidas duplicadas por accidente. Si usas varias máquinas conectadas a la misma base de datos, lo ideal es que cada una procese archivos o carpetas diferentes.
