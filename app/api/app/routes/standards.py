from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Standard
from app.schemas import StandardOut

router = APIRouter(tags=["standards"])


@router.get("/standards", response_model=list[StandardOut])
def list_standards(session: Session = Depends(get_session)) -> list[Standard]:
    return list(session.scalars(select(Standard).order_by(Standard.code)))
