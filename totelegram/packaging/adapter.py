import logging
from typing import List, cast

from totelegram.database import db_transaction
from totelegram.models import Job, Payload, TapeMember, TapeMemberGPS
from totelegram.packaging.schemas import VirtualChunk, VirtualTapeMember
from totelegram.utils import batched

logger = logging.getLogger(__name__)


class PackagingPersistenceAdapter:
    @staticmethod
    def persist_chunks(job: Job, virtual_chunks: List[VirtualChunk]) -> List[Payload]:
        """Guarda la estructura de payloads del archivo en la base de datos."""
        payloads_data = [
            {
                "job": job,
                "sequence_index": vc.sequence_index,
                "start_offset": vc.start_offset,
                "end_offset": vc.end_offset,
                "size": vc.size,
                "filename": vc.filename,
                "filename_short": vc.filename_short,
                "md5sum": vc.md5sum,
            }
            for vc in virtual_chunks
        ]

        with db_transaction(job._meta.database):
            Payload.insert_many(payloads_data).execute()

        return cast(List[Payload], list(job.payloads.order_by(Payload.sequence_index)))

    @staticmethod
    def persist_folder_chunks(
        job: Job,
        virtual_chunks: List[VirtualChunk],
        virtual_members: List[VirtualTapeMember],
    ) -> List[Payload]:
        """Guarda tanto la estructura de volúmenes como el índice interno de archivos."""
        database = job._meta.database

        with db_transaction(database):
            payloads_data = [
                {
                    "job": job,
                    "sequence_index": vc.sequence_index,
                    "start_offset": vc.start_offset,
                    "end_offset": vc.end_offset,
                    "size": vc.size,
                    "filename": vc.filename,
                    "filename_short": vc.filename_short,
                }
                for vc in virtual_chunks
            ]
            Payload.insert_many(payloads_data).execute()

            # Recuperar mapeo id <-> index de los payloads recién guardados
            payload_map = {
                p.sequence_index: p
                for p in cast(
                    list[Payload], job.payloads.order_by(Payload.sequence_index)
                )
            }

            member_inserts = [
                {
                    "source": job.source,
                    "relative_path": vm.relative_path,
                    "size": vm.size,
                    "md5sum": vm.md5sum,
                }
                for vm in virtual_members
            ]

            for batch in batched(member_inserts, 100):
                TapeMember.insert_many(batch).on_conflict_ignore().execute()

            # Obtener mapeo de ruta -> id para asociar los fragmentos (GPS)
            paths = [vm.relative_path for vm in virtual_members]
            db_members = TapeMember.select(
                TapeMember.id, TapeMember.relative_path
            ).where(
                (TapeMember.source == job.source) & (TapeMember.relative_path << paths)  # type: ignore
            )
            member_path_to_id = {m.relative_path: m.id for m in db_members}

            gps_inserts = []
            for vm in virtual_members:
                member_id = member_path_to_id.get(vm.relative_path)
                if not member_id:
                    continue

                for frag in vm.fragments:
                    payload_obj = payload_map.get(frag.vol_idx)
                    if not payload_obj:
                        continue

                    gps_inserts.append(
                        {
                            "member": member_id,
                            "payload": payload_obj,
                            "state": frag.state,
                            "offset_in_volume": frag.offset_in_vol,
                            "bytes_in_volume": frag.bytes_in_volume,
                        }
                    )

            for batch in batched(gps_inserts, 200):
                TapeMemberGPS.insert_many(batch).execute()

        return cast(List[Payload], list(job.payloads.order_by(Payload.sequence_index)))
