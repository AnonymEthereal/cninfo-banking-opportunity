import json
import os
from pathlib import Path
from typing import TypeVar, Type
from uuid import uuid4
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


def atomic_write_json(
    path: Path, value: BaseModel | dict, lease_guard=None
) -> None:
    if lease_guard is not None:
        lease_guard.assert_owned()
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(value, BaseModel):
        payload = value.model_dump()
    else:
        payload = value
    tmp = path.with_name(f"{path.name}.{uuid4().hex}.tmp")
    try:
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        if lease_guard is not None:
            with lease_guard.protect_write():
                os.replace(tmp, path)
        else:
            os.replace(tmp, path)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


def read_model(path: Path, model: Type[T]) -> T:
    return model.model_validate_json(path.read_text(encoding="utf-8"))
