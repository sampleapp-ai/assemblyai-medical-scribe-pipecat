import argparse
import asyncio
import os
from datetime import datetime

import httpx
from dotenv import load_dotenv
from loguru import logger

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
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
from pipecat.transports.daily.transport import DailyParams

load_dotenv(override=True)

# --- Shared state ---

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
    "You are a clinical transcription editor. "
    "Return ONLY the corrected text with no preamble, explanation, or commentary. "
    "Do NOT say things like 'I'm ready to help' or ask questions. "
    "Simply output the corrected version of the input text. "
    "Fix medical terminology (drug names, dosages, anatomy), proper nouns, "
    "and punctuation for readability. Preserve the speaker's original meaning "
    "and avoid inventing details. If the input is very short or unclear, "
    "return it as-is with minimal corrections."
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


class MedicalTranscriptProcessor(FrameProcessor):
    """Collect finalized transcripts, post-process via LLM Gateway, and broadcast to clients."""

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)

        if isinstance(frame, InterimTranscriptionFrame) and frame.text:
            logger.debug(f"[INTERIM] {frame.text}")
            from run import broadcast
            await broadcast({
                "type": "interim",
                "text": frame.text,
                "timestamp": datetime.now().strftime("%H:%M:%S"),
            })

        elif isinstance(frame, TranscriptionFrame) and frame.text:
            original = frame.text
            timestamp = datetime.now().strftime("%H:%M:%S")
            
            # Send original immediately for low latency display
            entry = {
                "type": "transcript",
                "text": original,
                "original": original,
                "timestamp": timestamp,
            }
            encounter_buffer.append(entry)
            from run import broadcast
            await broadcast(entry)
            logger.info(f"[SCRIBE] {original[:80]}...")

        await self.push_frame(frame, direction)


# --- Transport params (listen-only — no audio output) ---

transport_params = {
    "daily": lambda: DailyParams(
        audio_in_enabled=True,
        audio_out_enabled=False,
        vad_analyzer=SileroVADAnalyzer(
            params=VADParams(
                confidence=0.6,
                start_secs=0.1,
                stop_secs=0.8,  # Wait 800ms of silence before ending turn
                min_volume=0.4,
            )
        ),
    ),
    "webrtc": lambda: TransportParams(
        audio_in_enabled=True,
        audio_out_enabled=False,
        vad_analyzer=SileroVADAnalyzer(
            params=VADParams(
                confidence=0.6,
                start_secs=0.1,
                stop_secs=0.8,
                min_volume=0.4,
            )
        ),
    ),
}


async def run_example(transport: BaseTransport, _: argparse.Namespace, handle_sigint: bool):
    logger.info("Starting medical scribe")

    stt = AssemblyAISTTService(
        api_key=os.getenv("ASSEMBLYAI_API_KEY"),
        vad_force_turn_endpoint=True,  # Use VAD for turn detection with universal-streaming
        connection_params=AssemblyAIConnectionParams(
            speech_model="universal-streaming-english",
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
    """Register additional FastAPI routes for SOAP generation."""
    from fastapi.middleware.cors import CORSMiddleware

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

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
