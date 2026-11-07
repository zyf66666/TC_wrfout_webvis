"""Shared, per-plot-type variable styles. Updates merge atomically across browsers."""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ..core.access import require_write
from ..core.config import STORAGE_ROOT

router = APIRouter(prefix='/api/variable-preferences', tags=['variable-preferences'])
DB_PATH = STORAGE_ROOT / 'variable_preferences.sqlite'


class Preferences(BaseModel):
    variables: list[dict[str, Any]] = Field(default_factory=list, max_length=500)
    quiver: list[dict[str, Any]] = Field(default_factory=list, max_length=500)


def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute('CREATE TABLE IF NOT EXISTS preferences (kind TEXT PRIMARY KEY, value TEXT NOT NULL)')
    return conn


def merge_rows(previous, rows):
    result = list(previous)
    def key(row):
        return (row.get('name'), row.get('plot_type') or 'contourf')
    for row in rows:
        name = row.get('name')
        if not isinstance(name, str) or not name.strip():
            continue
        clean = {k: v for k, v in row.items() if k not in {'time', 'height', 'angle', 'radius'}}
        clean.update(name=name.strip(), enabled=False)
        index = next((i for i, item in enumerate(result) if key(item) == key(clean)), None)
        if index is None:
            result.append(clean)
        else:
            result[index] = clean
    return result


@router.get('')
def get_preferences():
    conn = connect()
    try:
        return JSONResponse({kind: json.loads(value) for kind, value in conn.execute('SELECT kind, value FROM preferences')},
                            headers={'Cache-Control': 'no-store'})
    finally:
        conn.close()


@router.put('/{kind}', dependencies=[Depends(require_write)])
def save_preferences(kind: str, payload: Preferences):
    if kind not in {str(i) for i in range(9)}:
        raise HTTPException(422, '未知图型')
    conn = connect()
    try:
        with conn:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute('SELECT value FROM preferences WHERE kind=?', (kind,)).fetchone()
            previous = json.loads(row[0]) if row else {}
            value = {field: merge_rows(previous.get(field, []), getattr(payload, field))
                     for field in ('variables', 'quiver')}
            conn.execute('INSERT OR REPLACE INTO preferences VALUES (?,?)', (kind, json.dumps(value, ensure_ascii=False)))
        return value
    finally:
        conn.close()
