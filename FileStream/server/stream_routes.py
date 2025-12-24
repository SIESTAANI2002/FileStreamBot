import math
import secrets
import logging
import mimetypes
import traceback
from aiohttp import web
from pyrogram.errors import RPCError, OffsetInvalid, FloodWait
from bot.core.database import db
from bot import Var, bot

routes = web.RouteTableDef()

# ==================================================================
# ✅ CORS Headers
# ==================================================================
def cors_headers():
    return {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS, HEAD",
        "Access-Control-Allow-Headers": "Content-Type, Range, User-Agent, X-Requested-With",
        "Access-Control-Expose-Headers": "Content-Length, Content-Range, Content-Disposition",
        "Access-Control-Max-Age": "86400",
    }

def setup_cors(app):
    pass

# ==================================================================
# 1. Root & API
# ==================================================================
@routes.get("/", allow_head=True)
async def root_route_handler(request):
    return web.json_response({"status": "running"}, headers=cors_headers())

@routes.get("/api/file/{id}")
async def api_handler(request):
    try:
        id = request.match_info['id']
        file_data = await db.get_file(id)
        if not file_data: 
            return web.json_response({"error": "File not found"}, status=404, headers=cors_headers())

        base_url = str(Var.URL).strip().strip("/")
        stream_url = f"{base_url}/watch/{id}"
        dl_link = f"{base_url}/dl/{id}"
        drive_link = f"https://drive.google.com/file/d/{file_data.get('drive_id')}/view" if file_data.get('drive_id') else None
        
        return web.json_response({
            "id": id,
            "title": file_data.get('anime_title', 'Unknown'),
            "stream_url": stream_url,
            "download_link": dl_link,
            "drive_link": drive_link,
            "poster": file_data.get('poster'),
            "genres": file_data.get('genres'),
            "quality": file_data.get('quality'),
        }, headers=cors_headers())
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500, headers=cors_headers())

# ==================================================================
# 2. FileStreamBot Style Stream Handler
# ==================================================================
@routes.options("/stream/{id}")
@routes.options("/watch/{id}")
@routes.options("/dl/{id}")
async def stream_options_handler(request):
    return web.Response(status=200, headers=cors_headers())

@routes.get("/stream/{id}")
@routes.get("/watch/{id}")
@routes.get("/dl/{id}")
async def stream_handler(request):
    try:
        id = request.match_info['id']
        file_data = await db.get_file(id)
        if not file_data: 
            return web.Response(text="❌ File Not Found", status=404, headers=cors_headers())

        # ✅ STEP 1: Get Real File Properties (Like FileStreamBot)
        msg_id = int(file_data.get('file_id') or file_data.get('message_id'))
        try:
            msg = await bot.get_messages(Var.FILE_STORE, msg_id)
            file_id = getattr(msg, msg.media.value)
            file_size = file_id.file_size
            file_name = getattr(file_id, "file_name", "video.mp4")
            mime_type = getattr(file_id, "mime_type", "video/mp4") or "video/mp4"
        except Exception:
            # Fallback if fetching fails
            file_size = int(file_data.get('file_size', 0))
            file_name = file_data.get('file_name', 'video.mp4')
            mime_type = "video/mp4"
            msg = None

        # ✅ STEP 2: Range Handling (Using aiohttp's native parser like FileStream)
        range_header = request.headers.get("Range", 0)
        
        if range_header:
            from_bytes, until_bytes = range_header.replace("bytes=", "").split("-")
            from_bytes = int(from_bytes)
            until_bytes = int(until_bytes) if until_bytes else file_size - 1
        else:
            # FileStreamBot Logic fallback
            from_bytes = 0
            until_bytes = file_size - 1

        # Strict Validation
        if (until_bytes >= file_size) or (from_bytes < 0) or (until_bytes < from_bytes):
            return web.Response(
                status=416,
                body="416: Range not satisfiable",
                headers={"Content-Range": f"bytes */{file_size}"},
            )

        # Calculation (Like FileStreamBot)
        req_length = until_bytes - from_bytes + 1
        chunk_size = 1024 * 1024 # 1 MB Chunk

        # Disposition Logic
        if "/dl/" in request.path:
            disposition = "attachment"
        else:
            disposition = "inline" # Force inline for playing

        # Headers Construction
        headers = cors_headers()
        headers.update({
            "Content-Type": f"{mime_type}",
            "Content-Range": f"bytes {from_bytes}-{until_bytes}/{file_size}",
            "Content-Length": str(req_length),
            "Content-Disposition": f'{disposition}; filename="{file_name}"',
            "Accept-Ranges": "bytes",
        })

        # ✅ STEP 3: The Generator (Mimicking ByteStreamer)
        async def media_streamer():
            message_to_stream = msg
            if not message_to_stream:
                try:
                    message_to_stream = await bot.get_messages(Var.FILE_STORE, msg_id)
                except: return

            total_yielded = 0
            
            try:
                # FileStreamBot তাদের নিজস্ব 'yield_file' ব্যবহার করে যা জটিল।
                # আমরা Pyrogram-এর 'stream_media' ব্যবহার করব কিন্তু 'limit' সেট করব
                # যাতে ব্রাউজার যতটুকু চেয়েছে ঠিক ততটুকুই পায়।
                
                async for chunk in bot.stream_media(message_to_stream, offset=from_bytes, limit=req_length):
                    yield chunk
                    total_yielded += len(chunk)
                    
                    # Safety Break
                    if total_yielded >= req_length:
                        break
                        
            except (RPCError, OffsetInvalid, FloodWait):
                pass
            except (OSError, ConnectionResetError, BrokenPipeError):
                pass
            except Exception:
                pass

        return web.Response(
            status=206 if range_header else 200,
            body=media_streamer(),
            headers=headers
        )
        
    except Exception as e:
        traceback.print_exc()
        return web.Response(text=f"Server Error: {e}", status=500, headers=cors_headers())
