"""周转等级手动更新入口。"""
from datetime import date
from zipfile import BadZipFile
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from starlette.concurrency import run_in_threadpool
from core.sku_turnover import read_latest, update

router = APIRouter(prefix="/api/sku-turnover", tags=["sku-turnover"])


@router.get("")
def latest():
    return read_latest()


async def _update(*args):
    try:
        return await run_in_threadpool(update, *args)
    except (ValueError, BadZipFile) as error:
        raise HTTPException(422, str(error)) from error


@router.post("/sample")
async def sample():
    return await _update()


@router.post("/update")
async def uploaded(file: UploadFile = File(...), end_date: date | None = Form(None)):
    return await _update(await file.read(), file.filename or "", end_date)
