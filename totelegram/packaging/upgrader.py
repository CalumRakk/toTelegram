"""
Motor de normalización y retrocompatibilidad hacia UploadManifest v5.0.
"""

import json
import lzma
import mimetypes
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from totelegram.packaging.schemas import UploadManifest


def _parse_yaml_text(text: str) -> Any:
    try:
        import yaml

        class SafeObjectLoader(yaml.SafeLoader):
            pass

        def _construct_mapping(loader, node):
            return loader.construct_mapping(node)

        # Manejadores para constructores de objetos de Python serializados en versiones previas
        for prefix in (
            "tag:yaml.org,2002:python/object:",
            "tag:yaml.org,2002:python/object/apply:",
            "tag:yaml.org,2002:python/object/new:",
            "!python/object:",
            "!python/object/apply:",
            "!python/object/new:",
        ):
            SafeObjectLoader.add_multi_constructor(
                prefix,
                lambda l, s, n: _construct_mapping(l, n),  # noqa: E741
            )

        return yaml.load(text, Loader=SafeObjectLoader)
    except ImportError:
        raise RuntimeError("PyYAML no instalado. Ejecuta: pip install pyyaml")


def derive_clean_source_name(path: Path) -> str:
    """Extrae el nombre base del recurso eliminando las extensiones conocidas de snapshots."""
    name = path.name
    # Eliminar extensiones compuestas y simples
    for ext in (".json.xz", ".yaml.xz", ".yml.xz", ".json", ".yaml", ".yml", ".xz"):
        if name.lower().endswith(ext):
            name = name[: -len(ext)]
            break
    return name


def load_raw_snapshot(path: Path) -> Any:
    name_lower = path.name.lower()

    # Archivos YAML planos
    if name_lower.endswith((".yaml", ".yml")):
        with open(path, "r", encoding="utf-8") as f:
            return _parse_yaml_text(f.read())

    # Archivos YAML comprimidos en XZ (.yaml.xz / .yml.xz)
    if name_lower.endswith((".yaml.xz", ".yml.xz")):
        with lzma.open(path, "rt", encoding="utf-8") as f:
            return _parse_yaml_text(f.read())

    # Archivos JSON comprimidos (.json.xz o genéricos .xz)
    if name_lower.endswith((".json.xz", ".xz")):
        with lzma.open(path, "rt", encoding="utf-8") as f:
            content = f.read()
            try:
                return json.loads(content)
            except json.JSONDecodeError:
                # Fallback: si dentro del .xz había un YAML antiguo
                return _parse_yaml_text(content)

    # JSON plano estándar
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return _parse_yaml_text(content)


def clean_part_suffix(filename: str) -> str:
    name = re.sub(r"_\d+-\d+$", "", filename)
    name = re.sub(r"\.\d+-\d+$", "", name)
    return name


def build_tg_link(chat_id: int, message_id: int) -> Optional[str]:
    s_id = str(chat_id)
    if s_id.startswith("-100"):
        return f"https://t.me/c/{s_id[4:]}/{message_id}"
    return None


def guess_mime(filename: str) -> str:
    mime, _ = mimetypes.guess_type(filename)
    return mime or "application/octet-stream"


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SnapshotUpgrader:
    @staticmethod
    def detect_version(data: Dict[str, Any]) -> str:
        if "manifest_version" in data and str(data["manifest_version"]) == "5.0":
            return "5.0"
        if "version" in data and data["version"] is not None:
            return str(data["version"])
        if "filedocuments" in data:
            return "legacy-filedocuments"
        if "file" in data and ("pieces" in data or "message" in data):
            return "legacy-pieces"
        return "unknown"

    @classmethod
    def upgrade_to_dict(
        cls,
        data: Dict[str, Any],
        source_path: Optional[Path] = None,
        tg_client: Any = None,
    ) -> Tuple[Dict[str, Any], str]:
        ver = cls.detect_version(data)

        if ver == "5.0":
            return data, "5.0 (nativo)"
        if ver == "4.0":
            res, trans = cls._upgrade_v4(data, source_path), "4.0 -> 5.0"
        elif ver == "3.0":
            res, trans = cls._upgrade_v3(data, source_path), "3.0 -> 5.0"
        elif ver == "1.0":
            res, trans = cls._upgrade_v1(data, source_path), "1.0 -> 5.0"
        elif ver in ("0.0.3", "0.0.2"):
            res, trans = (
                cls._upgrade_v0_0_x(data, source_path=source_path, tg_client=tg_client),
                f"{ver} -> 5.0",
            )
        elif ver == "0.0.1":
            res, trans = (
                cls._upgrade_v0_0_x(
                    data,
                    root_chat_id=data.get("chat_id"),
                    source_path=source_path,
                    tg_client=tg_client,
                ),
                "0.0.1 -> 5.0",
            )
        elif ver == "legacy-pieces":
            res, trans = (
                cls._upgrade_legacy_pieces(data, source_path),
                "legacy-pieces -> 5.0",
            )
        elif ver == "legacy-filedocuments":
            res, trans = (
                cls._upgrade_legacy_filedocuments(data, tg_client, source_path),
                "legacy-filedocuments -> 5.0",
            )
        else:
            origin = f" en '{source_path}'" if source_path else ""
            raise ValueError(f"Versión de snapshot no reconocida ({ver}){origin}")

        # Inyectar marca de trazabilidad del origen
        if source_path:
            res["_origin_snapshot"] = str(source_path.resolve())

        return res, trans

    @classmethod
    def load_and_upgrade(cls, path: Path, tg_client: Any = None) -> UploadManifest:
        raw_data = load_raw_snapshot(path)
        if not isinstance(raw_data, dict):
            raise ValueError(f"El archivo {path.name} no contiene un objeto válido.")

        v5_dict, transition = cls.upgrade_to_dict(raw_data, tg_client=tg_client)
        return UploadManifest.model_validate(v5_dict)

    @staticmethod
    def _upgrade_v4(
        data: Dict[str, Any], source_path: Optional[Path]
    ) -> Dict[str, Any]:
        source = data.get("source", {})
        source.setdefault("type", "file")
        source.setdefault("tape_catalog", None)
        source.setdefault("inventory", None)

        chat_id = data.get("target_chat_id") or data.get("chat_id")
        raw_parts = data.get("parts", [])
        total_parts = len(raw_parts)
        file_md5 = source.get("md5sum", "")
        parts = []
        offset = 0

        for idx, p in enumerate(raw_parts):
            p_size = p.get("part_size", 0)
            p_md5 = p.get("part_md5sum") or (file_md5 if total_parts == 1 else "")
            parts.append(
                {
                    "sequence": p.get("sequence", idx),
                    "message_id": p["message_id"],
                    "chat_id": p.get("chat_id", chat_id),
                    "link": p.get("link") or build_tg_link(chat_id, p["message_id"]),  # type: ignore
                    "part_filename": p.get("part_filename", source.get("filename")),
                    "part_size": p_size,
                    "part_md5sum": p_md5,
                    "start_offset": p.get("start_offset", offset),
                    "end_offset": p.get("end_offset", offset + p_size),
                }
            )
            offset += p_size

        return {
            "manifest_version": "5.0",
            "app_version": data.get("app_version", "0.9.22"),
            "created_at": data.get("created_at", iso_now()),
            "strategy": data.get(
                "strategy", "single-file" if len(parts) == 1 else "pieces-file"
            ),
            "chunk_size": data.get("chunk_size", 2097152000),
            "chat_id": chat_id,
            "owner_id": data.get("owner_id", 1),
            "owner_name": data.get("owner_name", "Legacy User"),
            "source": source,
            "parts": parts,
        }

    @staticmethod
    def _upgrade_v3(
        data: Dict[str, Any], source_path: Optional[Path]
    ) -> Dict[str, Any]:
        mgr = data.get("manager", {})
        f_info = mgr.get("file", {})
        kind = data.get("kind") or mgr.get("kind", "single-file")

        filename = f_info.get("filename", "unknown_file")
        total_size = f_info.get("size", 0)
        md5sum = f_info.get("md5sum", "")
        mime_type = f_info.get("mimeType") or guess_mime(filename)

        parts = []
        chat_id = None
        offset = 0

        if kind == "single-file":
            msg = mgr.get("message", {})
            chat_id = msg.get("chat_id", 0)
            parts.append(
                {
                    "sequence": 0,
                    "message_id": msg["message_id"],
                    "chat_id": chat_id,
                    "link": msg.get("link")
                    or build_tg_link(chat_id, msg["message_id"]),
                    "part_filename": filename,
                    "part_size": total_size,
                    "part_md5sum": md5sum,
                    "start_offset": 0,
                    "end_offset": total_size,
                }
            )
        else:
            pieces = mgr.get("pieces", [])
            for idx, p in enumerate(pieces):
                msg = p.get("message", {})
                if chat_id is None:
                    chat_id = msg.get("chat_id", 0)
                p_size = p.get("size", 0)
                parts.append(
                    {
                        "sequence": idx,
                        "message_id": msg["message_id"],
                        "chat_id": msg.get("chat_id", chat_id),
                        "link": msg.get("link")
                        or build_tg_link(chat_id, msg["message_id"]),
                        "part_filename": p.get("filename", f"{filename}_{idx + 1}"),
                        "part_size": p_size,
                        "part_md5sum": "",
                        "start_offset": offset,
                        "end_offset": offset + p_size,
                    }
                )
                offset += p_size

        return {
            "manifest_version": "5.0",
            "app_version": "0.9.22",
            "created_at": data.get("createdTime", iso_now()).replace(" ", "T"),
            "strategy": "single-file" if len(parts) == 1 else "pieces-file",
            "chunk_size": 2097152000,
            "chat_id": chat_id or 0,
            "owner_id": 1,
            "owner_name": "Legacy User",
            "source": {
                "filename": filename,
                "size": total_size,
                "md5sum": md5sum,
                "mime_type": mime_type,
                "mtime": 0.0,
                "type": "file",
                "tape_catalog": None,
                "inventory": None,
            },
            "parts": parts,
        }

    @staticmethod
    def _upgrade_v1(
        data: Dict[str, Any], source_path: Optional[Path]
    ) -> Dict[str, Any]:
        filename = data.get("filename", "unknown_file")
        total_size = data.get("size", 0)
        md5sum = data.get("md5sum", "")
        v_type = data.get("type", "single-file")

        parts = []
        chat_id = None
        offset = 0

        if v_type == "single-file":
            msg = data.get("message", {})
            chat_id = msg.get("chat_id", 0)
            parts.append(
                {
                    "sequence": 0,
                    "message_id": msg["message_id"],
                    "chat_id": chat_id,
                    "link": msg.get("link")
                    or build_tg_link(chat_id, msg["message_id"]),
                    "part_filename": filename,
                    "part_size": total_size,
                    "part_md5sum": md5sum,
                    "start_offset": 0,
                    "end_offset": total_size,
                }
            )
        else:
            pieces = data.get("pieces", [])
            for idx, p in enumerate(pieces):
                msg = p.get("message", {})
                if chat_id is None:
                    chat_id = msg.get("chat_id", 0)
                p_size = p.get("size", 0)
                parts.append(
                    {
                        "sequence": idx,
                        "message_id": msg["message_id"],
                        "chat_id": msg.get("chat_id", chat_id),
                        "link": msg.get("link")
                        or build_tg_link(chat_id, msg["message_id"]),
                        "part_filename": p.get("filename", f"{filename}_{idx + 1}"),
                        "part_size": p_size,
                        "part_md5sum": "",
                        "start_offset": offset,
                        "end_offset": offset + p_size,
                    }
                )
                offset += p_size

        return {
            "manifest_version": "5.0",
            "app_version": "0.9.22",
            "created_at": iso_now(),
            "strategy": "single-file" if len(parts) == 1 else "pieces-file",
            "chunk_size": 2097152000,
            "chat_id": chat_id or 0,
            "owner_id": 1,
            "owner_name": "Legacy User",
            "source": {
                "filename": filename,
                "size": total_size,
                "md5sum": md5sum,
                "mime_type": guess_mime(filename),
                "mtime": 0.0,
                "type": "file",
                "tape_catalog": None,
                "inventory": None,
            },
            "parts": parts,
        }

    @staticmethod
    def _upgrade_v0_0_x(
        data: Dict[str, Any],
        root_chat_id: Optional[int] = None,
        source_path: Optional[Path] = None,
        tg_client: Any = None,
    ) -> Dict[str, Any]:
        files = data.get("files", [])
        origin_str = f" en '{source_path}'" if source_path else ""

        if not files:
            raise ValueError(f"No se encontraron elementos en 'files'{origin_str}")

        first_file = files[0]

        # 1. Resolver el nombre sin caer ciegamente en 'unknown'
        sample_name = first_file.get("file_name") or first_file.get("filename")
        if not sample_name:
            if source_path:
                sample_name = derive_clean_source_name(source_path)
            else:
                sample_name = "unknown"

        clean_name = clean_part_suffix(sample_name)
        chat_id = root_chat_id or data.get("chat_id") or first_file.get("chat_id", 0)
        md5sum = data.get("md5sum", "")
        total_parts = len(files)

        parts = []
        offset = 0
        total_size = 0

        for idx, f in enumerate(files):
            p_size = f.get("size") or f.get("file_size") or 0
            p_chat = f.get("chat_id", chat_id)
            p_name = (
                f.get("file_name") or f.get("filename") or f"{clean_name}_{idx + 1}"
            )

            parts.append(
                {
                    "sequence": idx,
                    "message_id": f["message_id"],
                    "chat_id": p_chat,
                    "link": build_tg_link(p_chat, f["message_id"]),
                    "part_filename": p_name,
                    "part_size": p_size,
                    "part_md5sum": md5sum if total_parts == 1 else "",
                    "start_offset": offset,
                    "end_offset": offset + p_size,
                }
            )
            offset += p_size
            total_size += p_size

        # 2. VALIDACIÓN ESTRICTA (FAIL-FAST):
        # Si tiene piezas pero el tamaño total es 0, no permitir que continúe silenciosamente
        if total_size == 0 and len(parts) > 0:
            raise ValueError(
                f"Snapshot corrupto o incompleto{origin_str}: "
                f"Tiene {len(parts)} piezas registradas pero su tamaño es 0 bytes. "
                f"(Claves encontradas en primer item: {list(first_file.keys())})"
            )

        if not clean_name or clean_name.lower() == "unknown":
            raise ValueError(
                f"Snapshot corrupto{origin_str}: No fue posible determinar el nombre del recurso."
            )

        return {
            "manifest_version": "5.0",
            "app_version": "0.9.22",
            "created_at": iso_now(),
            "strategy": "single-file" if len(parts) == 1 else "pieces-file",
            "chunk_size": 2097152000,
            "chat_id": chat_id,
            "owner_id": 1,
            "owner_name": "Legacy User",
            "source": {
                "filename": clean_name,
                "size": total_size,
                "md5sum": md5sum,
                "mime_type": guess_mime(clean_name),
                "mtime": 0.0,
                "type": "file",
                "tape_catalog": None,
                "inventory": None,
            },
            "parts": parts,
        }

    @staticmethod
    def _upgrade_legacy_pieces(data: Dict[str, Any], tg_client: Any) -> Dict[str, Any]:
        f_info = data.get("file", {})
        filename = f_info.get("filename", "unknown_file")
        total_size = f_info.get("size", 0)
        md5sum = f_info.get("md5sum", "")

        pieces = data.get("pieces", [])
        total_parts = len(pieces)
        parts = []
        chat_id = None
        offset = 0

        for idx, p in enumerate(pieces):
            msg = p.get("message", {})
            p_file = p.get("file", {})
            if chat_id is None:
                chat_id = msg.get("chat_id", 0)

            p_size = p_file.get("size") or msg.get("size", 0)
            p_name = (
                p_file.get("filename")
                or msg.get("file_name")
                or f"{filename}_{idx + 1}"
            )
            p_md5 = p_file.get("md5sum") or (md5sum if total_parts == 1 else "")

            parts.append(
                {
                    "sequence": idx,
                    "message_id": msg["message_id"],
                    "chat_id": msg.get("chat_id", chat_id),
                    "link": msg.get("link")
                    or build_tg_link(chat_id, msg["message_id"]),
                    "part_filename": p_name,
                    "part_size": p_size,
                    "part_md5sum": p_md5,
                    "start_offset": offset,
                    "end_offset": offset + p_size,
                }
            )
            offset += p_size

        return {
            "manifest_version": "5.0",
            "app_version": "0.9.22",
            "created_at": iso_now(),
            "strategy": "single-file" if len(parts) == 1 else "pieces-file",
            "chunk_size": 2097152000,
            "chat_id": chat_id or 0,
            "owner_id": 1,
            "owner_name": "Legacy User",
            "source": {
                "filename": filename,
                "size": total_size,
                "md5sum": md5sum,
                "mime_type": guess_mime(filename),
                "mtime": 0.0,
                "type": "file",
                "tape_catalog": None,
                "inventory": None,
            },
            "parts": parts,
        }

    @staticmethod
    def _upgrade_legacy_filedocuments(
        data: Dict[str, Any], tg_client: Any = None, source_path: Optional[Path] = None
    ) -> Dict[str, Any]:
        chat_id = data.get("chat_id", 0)
        docs = data.get("filedocuments", [])
        md5sum = data.get("md5sum", "")
        total_parts = len(docs)

        if not docs:
            raise ValueError("No se encontraron elementos en 'filedocuments'")

        first_name = docs[0].get("filepart", "unknown_file")
        clean_name = clean_part_suffix(first_name)

        if tg_client is None:
            raise RuntimeError(
                f"El archivo '{clean_name}' (legacy-filedocuments) no contiene tamaños de pieza. "
                "Requiere conexión a Telegram para resolverse."
            )

        msg_ids = [d["message_id"] for d in docs]
        messages = tg_client.get_messages(chat_id, msg_ids)
        if not isinstance(messages, list):
            messages = [messages]

        msg_map = {m.id: m for m in messages if m and not getattr(m, "empty", False)}
        parts = []
        offset = 0
        total_size = 0

        for idx, doc in enumerate(docs):
            m_id = doc["message_id"]
            tg_msg = msg_map.get(m_id)
            if not tg_msg or not tg_msg.document:
                raise RuntimeError(
                    f"Mensaje {m_id} no encontrado en Telegram o sin documento."
                )

            p_size = tg_msg.document.file_size
            p_name = (
                doc.get("filepart")
                or tg_msg.document.file_name
                or f"{clean_name}_{idx + 1}"
            )

            parts.append(
                {
                    "sequence": idx,
                    "message_id": m_id,
                    "chat_id": chat_id,
                    "link": build_tg_link(chat_id, m_id),
                    "part_filename": p_name,
                    "part_size": p_size,
                    "part_md5sum": md5sum if total_parts == 1 else "",
                    "start_offset": offset,
                    "end_offset": offset + p_size,
                }
            )
            offset += p_size
            total_size += p_size

        return {
            "manifest_version": "5.0",
            "app_version": "0.9.22",
            "created_at": iso_now(),
            "strategy": "single-file" if len(parts) == 1 else "pieces-file",
            "chunk_size": 2097152000,
            "chat_id": chat_id,
            "owner_id": 1,
            "owner_name": "Legacy User",
            "source": {
                "filename": clean_name,
                "size": total_size,
                "md5sum": md5sum,
                "mime_type": guess_mime(clean_name),
                "mtime": 0.0,
                "type": "file",
                "tape_catalog": None,
                "inventory": None,
            },
            "parts": parts,
        }
