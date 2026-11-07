"""账号管理（只有 root 能用，2026-11-06）。网页顶栏“账号管理”对话框调用这里。"""
from __future__ import annotations

from typing import Any, List, Optional, Union

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..core.access import require_write
from ..storage import user_store as user_store_module
from ..storage.user_store import PAGES, UserError

router = APIRouter(prefix="/api/users", tags=["users"], dependencies=[Depends(require_write)])

DashboardChoice = Union[str, List[str]]


class NewUser(BaseModel):
    username: str = Field(max_length=64)
    password: str = Field(max_length=256)
    role: str = "user"
    can_annotate: bool = False
    can_view_plot_settings: bool = False
    can_plot: bool = False
    pages: List[str] = Field(default_factory=list)
    dashboards: DashboardChoice = Field(default_factory=list)


class UserChanges(BaseModel):
    role: Optional[str] = None
    can_annotate: Optional[bool] = None
    can_view_plot_settings: Optional[bool] = None
    can_plot: Optional[bool] = None
    pages: Optional[List[str]] = None
    dashboards: Optional[DashboardChoice] = None
    disabled: Optional[bool] = None


class PasswordReset(BaseModel):
    password: str = Field(max_length=256)


def _store():
    return user_store_module.store


def _call(function, *args, **kwargs) -> Any:
    try:
        return function(*args, **kwargs)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc).strip("'\"")) from exc
    except UserError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("")
def list_users():
    return {"users": _store().list_users(), "pages": PAGES}


@router.post("")
def create_user(payload: NewUser):
    return _call(_store().create_user, payload.username, payload.password, payload.role,
                 can_annotate=payload.can_annotate, pages=payload.pages, dashboards=payload.dashboards,
                 can_view_plot_settings=payload.can_view_plot_settings, can_plot=payload.can_plot)


@router.patch("/{username}")
def update_user(username: str, payload: UserChanges):
    return _call(_store().update_user, username, role=payload.role, can_annotate=payload.can_annotate,
                 pages=payload.pages, dashboards=payload.dashboards, disabled=payload.disabled,
                 can_view_plot_settings=payload.can_view_plot_settings, can_plot=payload.can_plot)


@router.post("/{username}/password")
def reset_password(username: str, payload: PasswordReset):
    _call(_store().set_password, username, payload.password)
    return {"status": "changed"}


@router.delete("/{username}")
def delete_user(username: str):
    _call(_store().delete_user, username)
    return {"status": "deleted"}
