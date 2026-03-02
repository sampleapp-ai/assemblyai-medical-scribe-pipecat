import argparse
import asyncio
import os
from datetime import datetime

import httpx
from dotenv import load_dotenv
from loguru import logger

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import InterimTranscriptionFrame, TranscriptionFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.frame_processor import FrameProcessor
from pipecat.services.assemblyai.stt import (
    AssemblyAIConnectionParams,
    AssemblyAISTTService,
)
from pipecat.transports.base_transport import BaseTransport, TransportParams
from pipecat.transports.services.daily import DailyParams

load_dotenv(override=True)

# --- Shared state for transcript broadcasting ---

transcript_queues: list[asyncio.Queue] = []
encounter_buffer: list[dict] = []

MEDICAL_KEYTERMS = [
    "hypertension", "diabetes mellitus", "coronary artery disease",
    "metformin 1000mg", "lisinopril 10mg", "atorvastatin 20mg",
    "chief complaint", "history of present illness", "review of systems",
    "physical examination", "assessment and plan",
    "auscultation", "palpation", "echocardiogram",
    "hemoglobin A1c", "blood pressure", "heart rate",
    "respiratory rate", "oxygen saturation", "body mass index",
]

LLM_GATEWAY_URL = "https://llm-gateway.assemblyai.com/v1/chat/completions"

MEDICAL_EDITING_SYSTEM_PROMPT = (
    "You are a clinical transcription editor. Your task: return ONLY the "
    "corrected text, with no preamble, explanation, or commentary. "
    "Fix medical terminology (drug names, dosages, anatomy), proper nouns, "
    "and punctuation for readability. Preserve the speaker's original meaning "
    "and avoid inventing details. Prefer U.S. clinical style. If a medication "
    "or condition is phonetically close, correct to the most likely clinical term."
)

SOAP_NOTE_SYSTEM_PROMPT = (
    "You are a clinician generating concise, structured notes. "
    "Produce a SOAP note (Subjective, Objective, Assessment, Plan). "
    "Use bullet points, keep it factual, infer reasonable clinical "
    "semantics from the transcript but do NOT invent data. Include "
    "medications with dosage and frequency if mentioned."
)


async def call_llm_gateway(system_prompt: str, user_content: str, max_tokens: int = 800) -> str | None:
    """Call AssemblyAI LLM Gateway for medical text processing."""
    headers = {
        "Authorization": os.getenv("ASSEMBLYAI_API_KEY", ""),
        "Content-Type": "application/json",
    }
    payload = {
        "model": os.getenv("LLM_GATEWAY_MODEL", "claude-haiku-4-5-20251001"),
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        "max_tokens": max_tokens,
        "temperature": 0.2,
    }
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(LLM_GATEWAY_URL, headers=headers, json=payload)
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.error(f"LLM Gateway error: {e}")
        return None


async def post_process_turn(text: str) -> str:
    """Post-process a transcription turn for medical accuracy."""
    result = await call_llm_gateway(MEDICAL_EDITING_SYSTEM_PROMPT, text, max_tokens=600)
    return result or text


async def generate_soap_note() -> str:
    """Generate a SOAP note from the full encounter transcript."""
    if not encounter_buffer:
        return "No transcript data available. Please ensure the encounter has started."
    transcript_text = "\n".join(
        f"[{entry['timestamp']}] {entry['text']}" for entry in encounter_buffer
    )
    result = await call_llm_gateway(
        SOAP_NOTE_SYSTEM_PROMPT,
        f"Create a SOAP note from this clinical encounter:\n\n{transcript_text}",
        max_tokens=1500,
    )
    return result or "Unable to generate SOAP note. Please try again."


async def broadcast_transcript(entry: dict):
    """Send a transcript entry to all connected WebSocket clients."""
    for queue in list(transcript_queues):
        try:
            queue.put_nowait(entry)
        except asyncio.QueueFull:
            pass


class MedicalTranscriptProcessor(FrameProcessor):
    """Collect finalized transcripts, post-process via LLM Gateway, and broadcast to clients."""

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)

        if isinstance(frame, InterimTranscriptionFrame) and frame.text:
            await broadcast_transcript({
                "type": "interim",
                "text": frame.text,
                "timestamp": datetime.now().strftime("%H:%M:%S"),
            })

        elif isinstance(frame, TranscriptionFrame) and frame.text:
            original = frame.text
            edited = await post_process_turn(original)
            entry = {
                "type": "transcript",
                "text": edited,
                "original": original,
                "timestamp": datetime.now().strftime("%H:%M:%S"),
            }
            encounter_buffer.append(entry)
            await broadcast_transcript(entry)
            logger.info(f"[SCRIBE] {edited[:80]}...")

        await self.push_frame(frame, direction)


# --- Transport params (listen-only — no audio output) ---

transport_params = {
    "daily": lambda: DailyParams(
        audio_in_enabled=True,
        audio_out_enabled=False,
        vad_analyzer=SileroVADAnalyzer(),
    ),
    "webrtc": lambda: TransportParams(
        audio_in_enabled=True,
        audio_out_enabled=False,
        vad_analyzer=SileroVADAnalyzer(),
    ),
}


async def run_example(transport: BaseTransport, _: argparse.Namespace, handle_sigint: bool):
    logger.info("Starting medical scribe")

    stt = AssemblyAISTTService(
        api_key=os.getenv("ASSEMBLYAI_API_KEY"),
        vad_force_turn_endpoint=False,
        connection_params=AssemblyAIConnectionParams(
            min_end_of_turn_silence_when_confident=800,
            max_turn_silence=3600,
            keyterms_prompt=MEDICAL_KEYTERMS,
        ),
    )

    processor = MedicalTranscriptProcessor()

    pipeline = Pipeline([
        transport.input(),
        stt,
        processor,
    ])

    task = PipelineTask(
        pipeline,
        params=PipelineParams(
            allow_interruptions=False,
            enable_metrics=True,
        ),
    )

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        logger.info("Client connected — listening for clinical encounter")

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        logger.info("Client disconnected")
        await task.cancel()

    runner = PipelineRunner(handle_sigint=handle_sigint)
    await runner.run(task)


def setup_routes(app):
    """Register additional FastAPI routes for transcript streaming and SOAP generation."""
    from fastapi import WebSocket as FastAPIWebSocket
    from fastapi.middleware.cors import CORSMiddleware

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.websocket("/ws/transcripts")
    async def transcript_websocket(websocket: FastAPIWebSocket):
        await websocket.accept()
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        transcript_queues.append(queue)

        async def send_loop():
            """Push transcript entries from the queue to the client."""
            try:
                for entry in encounter_buffer:
                    await websocket.send_json(entry)
                while True:
                    entry = await queue.get()
                    await websocket.send_json(entry)
            except Exception:
                pass

        async def recv_loop():
            """Listen for client commands (e.g. generate_soap)."""
            try:
                while True:
                    data = await websocket.receive_json()
                    if data.get("type") == "generate_soap":
                        await websocket.send_json({"type": "status", "message": "Generating SOAP note..."})
                        soap = await generate_soap_note()
                        await websocket.send_json({"type": "soap_note", "content": soap})
                        logger.info("SOAP note generated and sent to client")
            except Exception:
                pass

        try:
            await asyncio.gather(send_loop(), recv_loop())
        finally:
            if queue in transcript_queues:
                transcript_queues.remove(queue)

    @app.post("/api/soap")
    async def api_soap():
        soap = await generate_soap_note()
        return {"soap_note": soap}

    @app.post("/api/reset")
    async def api_reset():
        encounter_buffer.clear()
        return {"status": "ok"}


if __name__ == "__main__":
    from run import main

    main(run_example, transport_params=transport_params, setup_routes=setup_routes)
