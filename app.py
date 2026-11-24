from pathlib import Path
import os
import json
import re
import uuid
import logging

import certifi
import uvicorn
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
UPLOAD_DIR = BASE_DIR / "uploads"

load_dotenv(BASE_DIR / ".env")

os.environ["SSL_CERT_FILE"] = certifi.where()
os.environ["REQUESTS_CA_BUNDLE"] = certifi.where()

from fastapi import FastAPI, Request, UploadFile, File, Form
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool
from langchain_core.messages import (HumanMessage,
                                     AIMessage,
                                     AIMessageChunk,
                                     ToolMessage)
from agent import get_agent
from database import (init_db, list_conversations,
                      create_or_update_conversation,
                      get_chat_history,
                      save_chat_message)
from rag import add_document_to_rag
from tools import set_current_thread_id

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DATA_DIR.mkdir(exist_ok=True)
UPLOAD_DIR.mkdir(exist_ok=True)

TEMPLATES_DIR = BASE_DIR / "templates"
ALLOWED_EXTENSIONS = {".pdf", ".docx", ".txt", ".md", ".py", ".csv"}
MAX_UPLOAD_BYTES = 20 * 1024 * 1024

app = FastAPI()
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

init_db()



@app.get("/")
async def home(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={},
    )


@app.get("/conversations")
async def conversations():
    items = list_conversations()
    return{
    "conversations":[
    {
        "thread_id": item.thread_id,
        "title": item.title,
        "created_at": item.created_at.isoformat(),
        "updated_at": item.updated_at.isoformat()
    }
    for item in items
    ]
}



@app.get("/history/{thread_id}")
async def history(thread_id: str):
    messages = get_chat_history(thread_id)
    return {
        "messages": [
            {
                "role": msg.role,
                "content": msg.content,
            }
            for msg in messages
        ]

    }
           


@app.post("/upload")
async def upload_document(
    file: UploadFile = File(...),
    thread_id: str = Form(...),
):
    file_path: Path | None = None
    try:
        filename = Path((file.filename or "uploaded_file").replace("\\", "/")).name
        if filename in ("", ".", ".."):
            filename = "uploaded_file"
        safe_filename = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", filename).strip(" .")
        safe_filename = safe_filename[:150] or "uploaded_file"
        suffix = Path(safe_filename).suffix.lower()

        if suffix not in ALLOWED_EXTENSIONS:
            return JSONResponse(
                {
                    "success": False,
                    "message": "Unsupported file type. Upload PDF, DOCX, TXT, MD, PY or CSV.",
                },
                status_code=400,
            )

        contents = await file.read(MAX_UPLOAD_BYTES + 1)
        if len(contents) > MAX_UPLOAD_BYTES:
            return JSONResponse(
                {"success": False, "message": "File is too large. Maximum size is 20 MB."},
                status_code=413,
            )
        if not contents:
            return JSONResponse(
                {"success": False, "message": "Empty file."},
                status_code=400,
            )

        thread_id = (thread_id or "").strip() or "default"
        file_id = str(uuid.uuid4())
        file_path = UPLOAD_DIR / f"{file_id}_{safe_filename}"
        file_path.write_bytes(contents)

        create_or_update_conversation(thread_id, f"文档：{safe_filename}")

        result = await run_in_threadpool(
            add_document_to_rag,
            str(file_path),
            thread_id,
        )
        return JSONResponse(
            {
                "success": True,
                "filename": result["filename"],
                "chunks": result["chunks"],
                "message": (
                    f"Uploaded {result['filename']} and created "
                    f"{result['chunks']} chunks."
                ),
            }
        )
    except Exception as e:
        if file_path is not None:
            try:
                file_path.unlink(missing_ok=True)
            except OSError:
                pass
        logger.exception("Upload failed")
        return JSONResponse(
            {"success": False, "message": str(e)},
            status_code=500,
        )

def sse_data(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def should_stream_chunk(chunk, metadata) -> bool:
    metadata = metadata or {}
    node_name = str(metadata.get("langgraph_node", "")).lower()
    if "tool" in node_name:
        return False
    if isinstance(chunk, ToolMessage):
        return False
    if not isinstance(chunk, (AIMessage, AIMessageChunk)):
        return False
    if getattr(chunk, "tool_calls", None):
        return False
    if getattr(chunk, "invalid_tool_calls", None):
        return False
    additional_kwargs = getattr(chunk, "additional_kwargs", {}) or {}
    if additional_kwargs.get("tool_calls"):
        return False
    return True

def extract_text_from_chunk(chunk)->str:
    content=getattr(chunk, "content","")
    if not content:
        return ""
    if isinstance(content,str):
        return content
    if isinstance(content,list):
        text_parts = []
        for item in content:
            if isinstance(item,str):
                text_parts.append(item)

            elif isinstance(item,dict):
                if item.get("type") == "text" and isinstance(item.get("text"),str):
                    text_parts.append(item["text"])
                elif isinstance(item.get("text"),str):
                    text_parts.append(item["text"])
                elif isinstance(item.get("content"),str):
                    text_parts.append(item["content"])
        return"".join(text_parts)
    return ""


@app.post("/chat/stream")
async def chat_stream(request:Request):
    try:
        data = await request.json()
    except Exception:
        return JSONResponse(
            {"error": "Invalid JSON body."},
            status_code=400
        )
    user_message = str(data.get("message") or "")
    thread_id = str(data.get("thread_id") or "default").strip() or "default"
    selected_model = str(data.get("model") or "").strip() or None

    if not user_message.strip():
        return JSONResponse(
            {"error": "Message is required."},
            status_code=400
        )

    try:
        agent = get_agent(selected_model)
    except Exception:
        logger.exception("Failed to initialise the agent")
        return JSONResponse(
            {"error": "模型初始化失败，请检查 .env 中的 API Key 与模型配置。"},
            status_code=500,
        )

    create_or_update_conversation(thread_id, user_message)
    save_chat_message(thread_id, "user", user_message)

    set_current_thread_id(thread_id)

    config = {
        "configurable": {
            "thread_id": thread_id
        }
    }

    def event_generator():
        final_answer = ""

        try:
            inputs = {
                "messages": [
                    HumanMessage(content=user_message)
                ]
            }
            for chunk, metadata in agent.stream(inputs, 
                                                config=config,
                                                stream_mode="messages"):
                if not should_stream_chunk(chunk, metadata):
                    continue
                    
                token = extract_text_from_chunk(chunk)
                if token:
                    final_answer += token
                    yield sse_data({"token": token})
            if final_answer.strip():
                save_chat_message(thread_id, "assistant", final_answer)

            yield sse_data({"done": True})
        except Exception as e:
            logger.exception("Chat stream failed")
            yield sse_data({"error": str(e)})
            yield sse_data({"done": True})

    return StreamingResponse(
                            event_generator(), 
                            media_type="text/event-stream",
                            headers={
                                    "Cache-Control": "no-cache",
                                    "Connection": "keep-alive",
                                    "X-Accel-Buffering": "no"
                                }
                            )








if __name__ == "__main__":
    uvicorn.run(
        "app:app",
        host=os.getenv("APP_HOST", "127.0.0.1"),
        port=int(os.getenv("APP_PORT", "8080")),
        reload=os.getenv("APP_RELOAD", "1").lower() in {"1", "true", "yes"},
    )
