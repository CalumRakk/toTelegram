import logging

logger = logging.getLogger(__name__)
_PATCHED = False


def apply_pyrogram_patches():
    """Aplica parches quirúrgicos a Pyrogram para corregir comportamientos archivados."""
    global _PATCHED
    if _PATCHED:
        return

    import asyncio
    import functools
    import inspect
    import io
    import logging
    import math
    import os
    from hashlib import md5
    from pathlib import PurePath
    from typing import BinaryIO, Callable, Optional, Union

    import pyrogram
    from pyrogram import StopTransmission, __license__, __version__, enums, raw
    from pyrogram.errors import (
        ApiIdInvalid,
        BadRequest,
        SessionPasswordNeeded,
    )
    from pyrogram.session import Session  # type: ignore
    from pyrogram.types import TermsOfService, User
    from pyrogram.utils import ainput

    # --- Modifica Session SLEEP_THRESHOL ---
    # pyrogram/session/session.py

    Session.SLEEP_THRESHOLD = 60  # type: ignore Evita que pyrogram lanzara error con FloodWait cuando telegram pedia periodo corto de espera (11 en vez de menos de 10).
    logger.debug("Parche aplicado: Session.SLEEP_THRESHOLD = 60")

    # --- Reemplazar save_file (Captura de excepciones) ---

    # pyrogram/methods/advanced/save_file.py

    import pyrogram.methods.advanced.save_file

    async def save_file_patched(
        self: "pyrogram.Client",  # type: ignore
        path: Union[str, BinaryIO],
        file_id: Optional[int] = None,
        file_part: int = 0,
        progress: Optional[Callable] = None,
        progress_args: tuple = (),
    ):
        async with self.save_file_semaphore:
            if path is None:
                return None

            # Definimos la queue antes del worker, por las dudas.
            queue = asyncio.Queue(1)

            async def worker(session):
                while True:
                    data = await queue.get()

                    if data is None:
                        return
                    # Elimina try-except para que las excepciones no se silencien y puedan ser capturadas por el bloque externo
                    await session.invoke(data)
                    queue.task_done()

            part_size = 512 * 1024

            if isinstance(path, (str, PurePath)):
                fp = open(path, "rb")
            elif isinstance(path, io.IOBase):
                fp = path
            else:
                raise ValueError(
                    "Invalid file. Expected a file path as string or a binary (not text) file pointer"
                )

            file_name = getattr(fp, "name", "file.jpg")

            fp.seek(0, os.SEEK_END)
            file_size = fp.tell()
            fp.seek(0)

            if file_size == 0:
                raise ValueError("File size equals to 0 B")

            assert self.me is not None, "Client must be authorized to upload files"
            file_size_limit_mib = 4000 if self.me.is_premium else 2000

            if file_size > file_size_limit_mib * 1024 * 1024:
                raise ValueError(
                    f"Can't upload files bigger than {file_size_limit_mib} MiB"
                )

            file_total_parts = int(math.ceil(file_size / part_size))
            is_big = file_size > 10 * 1024 * 1024
            workers_count = 1
            is_missing_part = file_id is not None
            file_id = file_id or self.rnd_id()
            md5_sum = md5() if not is_big and not is_missing_part else None

            session = Session(
                self,
                await self.storage.dc_id(),  # type: ignore
                await self.storage.auth_key(),  # type: ignore
                await self.storage.test_mode(),  # type: ignore
                is_media=True,
            )

            workers = [
                self.loop.create_task(worker(session)) for _ in range(workers_count)
            ]
            try:
                await session.start()

                fp.seek(part_size * file_part)

                while True:
                    chunk = fp.read(part_size)

                    if not chunk:
                        if not is_big and not is_missing_part:
                            md5_sum = "".join(
                                [hex(i)[2:].zfill(2) for i in md5_sum.digest()]  # type: ignore
                            )
                        break

                    if is_big:
                        rpc = raw.functions.upload.SaveBigFilePart(  # type: ignore
                            file_id=file_id,
                            file_part=file_part,
                            file_total_parts=file_total_parts,
                            bytes=chunk,
                        )
                    else:
                        rpc = raw.functions.upload.SaveFilePart(  # type: ignore
                            file_id=file_id, file_part=file_part, bytes=chunk
                        )

                    await queue.put(rpc)

                    if is_missing_part:
                        return

                    if not is_big and not is_missing_part:
                        md5_sum.update(chunk)  # type: ignore

                    file_part += 1

                    if progress:
                        func = functools.partial(
                            progress,
                            min(file_part * part_size, file_size),
                            file_size,
                            *progress_args,
                        )

                        if inspect.iscoroutinefunction(progress):
                            await func()
                        else:
                            await self.loop.run_in_executor(self.executor, func)
            except StopTransmission:
                raise
            except Exception as e:
                logger.error(f"Error en save_file (parcheado): {e}")
                # Elevo la excepción para evitar ser silenciada, y con la esperanza que llegue a buen puerto.
                raise e

            else:
                if is_big:
                    return raw.types.InputFileBig(  # type: ignore
                        id=file_id,
                        parts=file_total_parts,
                        name=file_name,
                    )
                else:
                    return raw.types.InputFile(  # type: ignore
                        id=file_id,
                        parts=file_total_parts,
                        name=file_name,
                        md5_checksum=md5_sum,  # type: ignore
                    )
            finally:
                for _ in workers:
                    await queue.put(None)

                await asyncio.gather(*workers)

                await session.stop()

                if isinstance(path, (str, PurePath)):
                    fp.close()

    pyrogram.methods.advanced.save_file.SaveFile.save_file = save_file_patched  # type: ignore
    pyrogram.Client.save_file = save_file_patched  # type: ignore

    logger.debug("Parche aplicado: save_file")

    # --- Client.authorize ---

    # pyrogram/client.py

    async def authorize_patched(self) -> User:
        if self.bot_token:
            return await self.sign_in_bot(self.bot_token)

        print(f"Welcome to Pyrogram (version {__version__})")
        print(
            f"Pyrogram is free software and comes with ABSOLUTELY NO WARRANTY. Licensed\n"
            f"under the terms of the {__license__}.\n"
        )

        while True:
            try:
                if not self.phone_number:
                    while True:
                        value = await ainput("Enter phone number or bot token: ")

                        if not value:
                            continue

                        confirm = (
                            await ainput(f'Is "{value}" correct? (y/N): ')
                        ).lower()

                        if confirm == "y":
                            break

                    if ":" in value:
                        self.bot_token = value
                        return await self.sign_in_bot(value)
                    else:
                        self.phone_number = value

                sent_code = await self.send_code(self.phone_number)

            # excepcion añadida para capturar el error de api_id invalido y advertir al usuario, en lugar de caer en un bucle infinito de intentos
            except ApiIdInvalid as e:
                print(e.MESSAGE)
                raise e
            except BadRequest as e:
                print(e.MESSAGE)
                self.phone_number = None
                self.bot_token = None
            else:
                break

        sent_code_descriptions = {
            enums.SentCodeType.APP: "Telegram app",
            enums.SentCodeType.SMS: "SMS",
            enums.SentCodeType.CALL: "phone call",
            enums.SentCodeType.FLASH_CALL: "phone flash call",
            enums.SentCodeType.FRAGMENT_SMS: "Fragment SMS",
            enums.SentCodeType.EMAIL_CODE: "email code",
        }

        print(
            f"The confirmation code has been sent via {sent_code_descriptions[sent_code.type]}"
        )

        while True:
            if not self.phone_code:
                self.phone_code = await ainput("Enter confirmation code: ")

            try:
                signed_in = await self.sign_in(
                    self.phone_number, sent_code.phone_code_hash, self.phone_code
                )
            except BadRequest as e:
                print(e.MESSAGE)
                self.phone_code = None
            except SessionPasswordNeeded as e:
                print(e.MESSAGE)

                while True:
                    print("Password hint: {}".format(await self.get_password_hint()))

                    if not self.password:
                        self.password = await ainput(
                            "Enter password (empty to recover): ",
                            hide=self.hide_password,
                        )

                    try:
                        if not self.password:
                            confirm = await ainput("Confirm password recovery (y/n): ")

                            if confirm == "y":
                                email_pattern = await self.send_recovery_code()
                                print(
                                    f"The recovery code has been sent to {email_pattern}"
                                )

                                while True:
                                    recovery_code = await ainput(
                                        "Enter recovery code: "
                                    )

                                    try:
                                        return await self.recover_password(
                                            recovery_code
                                        )
                                    except BadRequest as e:
                                        print(e.MESSAGE)
                                    except Exception as e:
                                        logger.exception(e)
                                        raise
                            else:
                                self.password = None
                        else:
                            return await self.check_password(self.password)
                    except BadRequest as e:
                        print(e.MESSAGE)
                        self.password = None
            else:
                break

        if isinstance(signed_in, User):
            return signed_in

        while True:
            first_name = await ainput("Enter first name: ")
            last_name = await ainput("Enter last name (empty to skip): ")

            try:
                signed_up = await self.sign_up(
                    self.phone_number, sent_code.phone_code_hash, first_name, last_name
                )
            except BadRequest as e:
                print(e.MESSAGE)
            else:
                break

        if isinstance(signed_in, TermsOfService):
            print("\n" + signed_in.text + "\n")
            await self.accept_terms_of_service(signed_in.id)

        return signed_up

    pyrogram.Client.authorize = authorize_patched  # type: ignore

    # --- MARCA DE AGUA  ---

    _PATCHED = True
    setattr(pyrogram.Client, "_patched_by_totelegram", True)  # type: ignore
    logger.debug("Core: Pyrogram Runtime Patches aplicados exitosamente.")
    logger.debug("Parches aplicados correctamente.")


def get_patch_status() -> dict:
    """
    Devuelve un reporte detallado del estado de los parches.
    No requiere que el parche haya sido aplicado previamente.
    """
    try:
        import pyrogram
        from pyrogram.session import Session  # type: ignore

        is_patched = getattr(pyrogram.Client, "_patched_by_totelegram", False)  # type: ignore
        is_save_file_custom = pyrogram.Client.save_file.__name__ == "save_file_patched"  # type: ignore

        return {
            "applied": is_patched,
            "save_file_integrity": is_save_file_custom,
            "sleep_threshold": getattr(Session, "SLEEP_THRESHOLD", None),
        }
    except ImportError:
        return {"applied": False, "error": "Pyrogram no instalado"}
