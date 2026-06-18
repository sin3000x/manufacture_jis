from __future__ import annotations

import asyncio
import base64
import json
import tempfile
import uuid
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, File, Form, Request, UploadFile, WebSocket, WebSocketDisconnect
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
_LOG_QUEUE: asyncio.Queue[dict[str, str]] | None = None
_LOG_SINK_ID: int | None = None
_LOG_CLIENTS: set[WebSocket] = set()


class _WebSocketLogSink:
    def __call__(self, message) -> None:
        record = message.record
        payload = {
            "time": record["time"].strftime("%Y-%m-%d %H:%M:%S"),
            "level": record["level"].name,
            "message": record["message"],
            "name": record["name"],
        }
        _push_log_event(payload)


@app.on_event("startup")
async def startup() -> None:
    global _LOG_QUEUE, _LOG_SINK_ID
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    _LOG_QUEUE = asyncio.Queue(maxsize=500)
    _LOG_SINK_ID = logger.add(
        _WebSocketLogSink(),
        level="INFO",
        enqueue=True,
        backtrace=False,
        diagnose=False,
        catch=False,
    )
    asyncio.create_task(_log_broker())


@app.on_event("shutdown")
def shutdown() -> None:
    global _LOG_SINK_ID
    if _LOG_SINK_ID is not None:
        logger.remove(_LOG_SINK_ID)
        _LOG_SINK_ID = None


@app.get("/")
def index(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "default_lead_time": 8,
            "default_lag_time": 3,
            "default_interval_hours": 4,
            "default_rest_times": [1, 2, 7, 12, 13, 18],
        },
    )


@app.websocket("/ws/logs")
async def log_stream(websocket: WebSocket):
    await websocket.accept()
    _LOG_CLIENTS.add(websocket)
    try:
        await websocket.send_json({"type": "status", "message": "日志连接已建立"})
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        _LOG_CLIENTS.discard(websocket)


@app.post("/solve")
async def solve(
    file: UploadFile = File(...),
    arrival_lead_time: int = Form(8),
    arrival_lag_time: int = Form(3),
    consumption_interval_hours: int = Form(1),
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
    if consumption_interval_hours < 1:
        return JSONResponse(
            status_code=422,
            content={"error": "任务令拆分小时数必须大于 0"},
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
            consumption_interval_hours=consumption_interval_hours,
        )

        result = await asyncio.to_thread(_run_solver, data)

        result.write_excel(output_path)

        download_name = f"result_{Path(file.filename).stem}.xlsx"
        result_bytes = output_path.read_bytes()

        response_headers: dict[str, str] = {
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(download_name)}",
        }
        if result.issues:
            issues_json = json.dumps(result.issues, ensure_ascii=False)
            response_headers["X-JIS-Issues-B64"] = base64.b64encode(issues_json.encode("utf-8")).decode("ascii")

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


def _push_log_event(payload: dict[str, str]) -> None:
    if _LOG_QUEUE is None:
        return
    try:
        _LOG_QUEUE.put_nowait(payload)
    except asyncio.QueueFull:
        pass


async def _log_broker() -> None:
    if _LOG_QUEUE is None:
        return
    while True:
        payload = await _LOG_QUEUE.get()
        dead_clients: set[WebSocket] = set()
        for client in list(_LOG_CLIENTS):
            try:
                await client.send_json({"type": "log", **payload})
            except Exception:
                dead_clients.add(client)
        for client in dead_clients:
            _LOG_CLIENTS.discard(client)


def _cleanup(path: Path) -> None:
    try:
        if path.exists():
            path.unlink()
    except OSError:
        pass
