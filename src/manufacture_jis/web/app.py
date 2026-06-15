from __future__ import annotations

import asyncio
import json
import tempfile
import uuid
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from loguru import logger

from manufacture_jis.jis_data import JISData
from manufacture_jis.jis_cp_model import JISCPModel

APP_DIR = Path(__file__).resolve().parent
app = FastAPI(title="JIS 物料配送优化系统")

app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(APP_DIR / "templates"))

UPLOAD_DIR = Path(tempfile.gettempdir()) / "jis_uploads"
DEFAULT_REST_TIMES = "1,2,7,12,13,18"


@app.on_event("startup")
def startup() -> None:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


@app.get("/")
def index(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "default_lead_time": 8,
            "default_lag_time": 3,
            "default_rest_times": [1, 2, 7, 12, 13, 18],
        },
    )


@app.post("/solve")
async def solve(
    file: UploadFile = File(...),
    arrival_lead_time: int = Form(8),
    arrival_lag_time: int = Form(3),
    rest_times: str = Form(DEFAULT_REST_TIMES),
):
    if not file.filename or not file.filename.lower().endswith((".xlsx", ".xls")):
        return JSONResponse(
            status_code=422,
            content={"error": "请上传 .xlsx 或 .xls 文件"},
        )

    rest_times_list = _parse_rest_times(rest_times)
    if rest_times_list is None:
        return JSONResponse(
            status_code=422,
            content={"error": "休息时段格式错误，请输入 0-23 的整数，逗号分隔"},
        )

    if arrival_lead_time < 1:
        return JSONResponse(
            status_code=422,
            content={"error": "到达提前期必须大于 0"},
        )
    if arrival_lag_time < 1:
        return JSONResponse(
            status_code=422,
            content={"error": "到达滞后期必须大于 0"},
        )

    input_path = UPLOAD_DIR / f"{uuid.uuid4().hex}_{file.filename}"
    output_path = UPLOAD_DIR / f"{uuid.uuid4().hex}_result.xlsx"

    try:
        content = await file.read()
        input_path.write_bytes(content)

        data = JISData(
            path=input_path,
            arrival_lead_time=arrival_lead_time,
            arrival_lag_time=arrival_lag_time,
            rest_times=rest_times_list,
        )

        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, _run_solver, data)

        result.write_excel(output_path)

        download_name = f"result_{Path(file.filename).stem}.xlsx"
        result_bytes = output_path.read_bytes()

        response_headers: dict[str, str] = {
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(download_name)}",
        }
        if result.issues:
            response_headers["X-JIS-Issues"] = json.dumps(result.issues, ensure_ascii=False)

        return Response(
            content=result_bytes,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers=response_headers,
        )

    except ValueError as e:
        return JSONResponse(status_code=422, content={"error": str(e)})
    except RuntimeError as e:
        return JSONResponse(
            status_code=422,
            content={"error": f"求解器未找到可行解，请检查输入数据或调整参数。详情：{e}"},
        )
    except Exception as e:
        logger.exception("Unexpected error during solve")
        return JSONResponse(
            status_code=500,
            content={"error": f"服务器内部错误：{e}"},
        )
    finally:
        _cleanup(input_path)
        _cleanup(output_path)


def _parse_rest_times(raw: str) -> list[int] | None:
    try:
        result = [int(x.strip()) for x in raw.split(",") if x.strip()]
        if any(t < 0 or t > 23 for t in result):
            return None
        return result
    except ValueError:
        return None


def _run_solver(data: JISData):
    model = JISCPModel(data)
    return model.run()


def _cleanup(path: Path) -> None:
    try:
        if path.exists():
            path.unlink()
    except OSError:
        pass
