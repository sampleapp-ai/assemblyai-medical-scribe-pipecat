import argparse
import asyncio
import json
import os
import sys
import uuid
from contextlib import asynccontextmanager
from typing import Any, Callable, Dict, Mapping, Optional

import aiohttp
import time as _time
import uvicorn
from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, Request, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse
from loguru import logger

from pipecat.serializers.twilio import TwilioFrameSerializer
from pipecat.transports.base_transport import BaseTransport, TransportParams
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport
from pipecat.transports.smallwebrtc.connection import IceServer, SmallWebRTCConnection
from pipecat.transports.daily.transport import DailyParams, DailyTransport

load_dotenv(override=True)

_websocket_clients: set = set()


async def broadcast(data: dict):
    """Broadcast a JSON message to all connected WebSocket clients."""
    disconnected = set()
    message = json.dumps(data)
    for ws in _websocket_clients:
        try:
            await ws.send_text(message)
        except Exception:
            disconnected.add(ws)
    _websocket_clients.difference_update(disconnected)


def get_transport_client_id(transport: BaseTransport, client: Any) -> str:
    if isinstance(transport, SmallWebRTCTransport):
        return client.pc_id
    elif isinstance(transport, DailyTransport):
        return client["id"]
    logger.warning(f"Unable to get client id from unsupported transport {type(transport)}")
    return ""


async def maybe_capture_participant_camera(
    transport: BaseTransport, client: Any, framerate: int = 0
):
    if isinstance(transport, DailyTransport):
        await transport.capture_participant_video(
            client["id"], framerate=framerate, video_source="camera"
        )


async def maybe_capture_participant_screen(
    transport: BaseTransport, client: Any, framerate: int = 0
):
    if isinstance(transport, DailyTransport):
        await transport.capture_participant_video(
            client["id"], framerate=framerate, video_source="screenVideo"
        )


def run_example_daily(
    run_example: Callable,
    args: argparse.Namespace,
    params: DailyParams,
    setup_routes: Optional[Callable] = None,
):
    logger.info("Running example with DailyTransport (web server mode)...")

    app = FastAPI()

    daily_api_key = os.getenv("DAILY_API_KEY")
    if not daily_api_key:
        logger.error("DAILY_API_KEY environment variable is required for daily transport")
        return

    if setup_routes:
        setup_routes(app)

    @app.get("/", include_in_schema=False)
    async def root():
        return {"status": "ok", "app": "medical-scribe-pipecat"}

    @app.websocket("/ws/transcripts")
    async def transcript_ws(websocket: WebSocket):
        await websocket.accept()
        _websocket_clients.add(websocket)
        logger.info("Transcript WebSocket client connected")
        try:
            while True:
                await websocket.receive_text()
        except Exception:
            pass
        finally:
            _websocket_clients.discard(websocket)
            logger.info("Transcript WebSocket client disconnected")

    @app.post("/api/create-room")
    async def create_room(background_tasks: BackgroundTasks):
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.daily.co/v1/rooms",
                headers={"Authorization": f"Bearer {daily_api_key}"},
                json={
                    "properties": {
                        "exp": int(_time.time()) + 3600,
                        "enable_chat": False,
                        "start_video_off": True,
                    }
                },
            ) as resp:
                if resp.status != 200:
                    error = await resp.text()
                    logger.error(f"Failed to create Daily room: {error}")
                    return {"error": "Failed to create room"}
                room_data = await resp.json()

            room_url = room_data["url"]
            room_name = room_data["name"]
            logger.info(f"Created Daily room: {room_url}")

            async with session.post(
                "https://api.daily.co/v1/meeting-tokens",
                headers={"Authorization": f"Bearer {daily_api_key}"},
                json={
                    "properties": {
                        "room_name": room_name,
                        "is_owner": False,
                    }
                },
            ) as resp:
                if resp.status != 200:
                    error = await resp.text()
                    logger.error(f"Failed to create Daily token: {error}")
                    return {"error": "Failed to create token"}
                token_data = await resp.json()

            user_token = token_data["token"]

            async with session.post(
                "https://api.daily.co/v1/meeting-tokens",
                headers={"Authorization": f"Bearer {daily_api_key}"},
                json={
                    "properties": {
                        "room_name": room_name,
                        "is_owner": True,
                    }
                },
            ) as resp:
                bot_token_data = await resp.json()
                bot_token = bot_token_data["token"]

        async def start_bot():
            transport = DailyTransport(room_url, bot_token, "Medical Scribe Bot", params=params)
            await run_example(transport, args, False)

        background_tasks.add_task(start_bot)

        return {"room_url": room_url, "token": user_token}

    uvicorn.run(app, host=args.host, port=args.port)


def run_example_webrtc(
    run_example: Callable,
    args: argparse.Namespace,
    params: TransportParams,
    setup_routes: Optional[Callable] = None,
):
    logger.info("Running example with SmallWebRTCTransport...")

    app = FastAPI()

    if setup_routes:
        setup_routes(app)

    pcs_map: Dict[str, SmallWebRTCConnection] = {}

    ice_servers = [
        IceServer(
            urls="stun:stun.l.google.com:19302",
        )
    ]

    turn_url = os.getenv("TURN_URL")
    turn_username = os.getenv("TURN_USERNAME")
    turn_credential = os.getenv("TURN_CREDENTIAL")
    if turn_url:
        ice_servers.append(
            IceServer(
                urls=turn_url,
                username=turn_username or "",
                credential=turn_credential or "",
            )
        )

    @app.get("/", include_in_schema=False)
    async def root():
        return {"status": "ok", "app": "medical-scribe-pipecat"}

    @app.get("/api/ice-servers")
    async def get_ice_servers():
        servers = [{"urls": "stun:stun.l.google.com:19302"}]
        if turn_url:
            servers.append({
                "urls": turn_url,
                "username": turn_username or "",
                "credential": turn_credential or "",
            })
        return servers

    @app.websocket("/ws/transcripts")
    async def transcript_ws(websocket: WebSocket):
        await websocket.accept()
        _websocket_clients.add(websocket)
        logger.info("Transcript WebSocket client connected")
        try:
            while True:
                await websocket.receive_text()
        except Exception:
            pass
        finally:
            _websocket_clients.discard(websocket)
            logger.info("Transcript WebSocket client disconnected")

    sessions_map: Dict[str, SmallWebRTCConnection] = {}

    @app.post("/start")
    async def start():
        session_id = str(uuid.uuid4())
        logger.info(f"Starting new session: {session_id}")
        return {"session_id": session_id}

    @app.post("/sessions/{session_id}/api/offer")
    async def session_offer_post(session_id: str, request: Request, background_tasks: BackgroundTasks):
        body = await request.json()
        logger.info(f"POST session offer body keys: {list(body.keys())}")

        pipecat_connection = SmallWebRTCConnection(ice_servers)
        await pipecat_connection.initialize(sdp=body["sdp"], type=body["type"])

        @pipecat_connection.event_handler("closed")
        async def handle_disconnected(webrtc_connection: SmallWebRTCConnection):
            logger.info(f"Discarding peer connection for pc_id: {webrtc_connection.pc_id}")
            pcs_map.pop(webrtc_connection.pc_id, None)
            sessions_map.pop(session_id, None)

        transport = SmallWebRTCTransport(params=params, webrtc_connection=pipecat_connection)
        background_tasks.add_task(run_example, transport, args, False)

        answer = pipecat_connection.get_answer()
        pcs_map[answer["pc_id"]] = pipecat_connection
        sessions_map[session_id] = pipecat_connection

        return answer

    @app.patch("/sessions/{session_id}/api/offer")
    async def session_offer_patch(session_id: str, request: Request):
        body = await request.json()
        logger.info(f"PATCH session offer body keys: {list(body.keys())}")

        pc_id = body.get("pc_id")
        candidates = body.get("candidates", [])

        pipecat_connection = pcs_map.get(pc_id) or sessions_map.get(session_id)
        if not pipecat_connection:
            logger.error(f"No connection found for session {session_id}")
            return {"error": "No connection found"}

        for candidate in candidates:
            await pipecat_connection.add_ice_candidate(candidate)

        return {"status": "ok"}

    @app.post("/api/offer")
    async def offer(request: dict, background_tasks: BackgroundTasks):
        pc_id = request.get("pc_id")

        if pc_id and pc_id in pcs_map:
            pipecat_connection = pcs_map[pc_id]
            logger.info(f"Reusing existing connection for pc_id: {pc_id}")
            await pipecat_connection.renegotiate(
                sdp=request["sdp"],
                type=request["type"],
                restart_pc=request.get("restart_pc", False),
            )
        else:
            pipecat_connection = SmallWebRTCConnection(ice_servers)
            await pipecat_connection.initialize(sdp=request["sdp"], type=request["type"])

            @pipecat_connection.event_handler("closed")
            async def handle_disconnected(webrtc_connection: SmallWebRTCConnection):
                logger.info(f"Discarding peer connection for pc_id: {webrtc_connection.pc_id}")
                pcs_map.pop(webrtc_connection.pc_id, None)

            transport = SmallWebRTCTransport(params=params, webrtc_connection=pipecat_connection)
            background_tasks.add_task(run_example, transport, args, False)

        answer = pipecat_connection.get_answer()
        pcs_map[answer["pc_id"]] = pipecat_connection

        return answer

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        coros = [pc.close() for pc in pcs_map.values()]
        await asyncio.gather(*coros)
        pcs_map.clear()

    uvicorn.run(app, host=args.host, port=args.port)


def run_main(
    run_example: Callable,
    args: argparse.Namespace,
    transport_params: Mapping[str, Callable] = {},
    setup_routes: Optional[Callable] = None,
):
    if args.transport not in transport_params:
        logger.error(f"Transport '{args.transport}' not supported by this example")
        return

    params = transport_params[args.transport]()
    match args.transport:
        case "daily":
            run_example_daily(run_example, args, params, setup_routes=setup_routes)
        case "webrtc":
            run_example_webrtc(run_example, args, params, setup_routes=setup_routes)


def main(
    run_example: Callable,
    *,
    parser: Optional[argparse.ArgumentParser] = None,
    transport_params: Mapping[str, Callable] = {},
    setup_routes: Optional[Callable] = None,
):
    if not parser:
        parser = argparse.ArgumentParser(description="Pipecat Bot Runner")
    parser.add_argument(
        "--host", default="localhost", help="Host for HTTP server (default: localhost)"
    )
    parser.add_argument(
        "--port", type=int, default=7860, help="Port for HTTP server (default: 7860)"
    )
    parser.add_argument(
        "--transport",
        "-t",
        type=str,
        choices=["daily", "webrtc"],
        default="daily",
        help="The transport this example should use",
    )
    parser.add_argument("--verbose", "-v", action="count", default=0)
    args = parser.parse_args()

    logger.remove(0)
    logger.add(sys.stderr, level="TRACE" if args.verbose else "DEBUG")

    run_main(run_example, args, transport_params, setup_routes=setup_routes)
