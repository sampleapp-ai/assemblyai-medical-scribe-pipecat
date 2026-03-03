# Medical Scribe — Real-Time Ambient Clinical Documentation

A real-time ambient clinical documentation agent built with [Pipecat](https://github.com/pipecat-ai/pipecat) and [AssemblyAI](https://www.assemblyai.com/). The agent passively listens to doctor-patient conversations, transcribes speech in real time with medical terminology boosting, and automatically generates structured SOAP notes at session end.

**This is a passive, listen-only scribe** — it captures and documents without interrupting the clinical workflow.

## How It Works

```
┌─────────────┐     ┌──────────────────────┐     ┌─────────────────┐     ┌────────────┐
│  Microphone │────▶│  AssemblyAI STT      │────▶│  LLM Gateway    │────▶│  Client UI │
│             │     │  (Universal Streaming)│     │  (Terminology   │     │            │
└─────────────┘     └──────────────────────┘     │   Correction)   │     └────────────┘
                                                 └─────────────────┘            │
                                                                                ▼
                                                                         ┌────────────┐
                                                                         │ SOAP Note  │
                                                                         │ Generation │
                                                                         └────────────┘
```

| Component | Technology | Purpose |
|-----------|------------|---------|
| **Speech-to-Text** | AssemblyAI Universal Streaming | Sub-300ms streaming transcription with medical keyterms boosting |
| **Post-Processing** | AssemblyAI LLM Gateway | Per-turn medical terminology correction (drug names, dosages, anatomy) |
| **Transport** | Pipecat (Daily/WebRTC) | Real-time audio streaming (input only) |
| **Frontend** | Next.js | Live transcript display with interim results and SOAP note panel |

## Features

- **Ambient listening** — Passively transcribes without TTS or voice responses, preserving natural clinical conversation flow
- **Medical keyterms boosting** — Domain-specific vocabulary (medications, conditions, procedures) boosted for higher accuracy
- **Conservative turn detection** — 800ms silence threshold tuned for natural clinical pauses where speakers take longer breaks
- **Real-time LLM correction** — Each finalized turn is post-processed to fix medical terminology, drug names, and dosages
- **Automatic SOAP notes** — Generates structured Subjective/Objective/Assessment/Plan documentation on session end
- **Live interim transcripts** — Shows partial results as speech is being recognized for immediate feedback

## Prerequisites

- Python 3.10+
- Node.js 18+
- [AssemblyAI API key](https://www.assemblyai.com/dashboard/signup) (powers both STT and LLM Gateway)

## Quick Start

### 1. Server Setup

```bash
cd medical-scribe/pipecat

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env and add your ASSEMBLYAI_API_KEY
```

### 2. Client Setup

```bash
cd client
npm install
cp .env.example .env.local
```

### 3. Run the Application

Start the server:

```bash
python medical_scribe.py
```

In a separate terminal, start the client:

```bash
cd client
npm run dev
```

Open http://localhost:3000 in your browser. Click **Start Recording** to begin capturing the clinical encounter. The transcript updates in real time. Click **End Session** to generate the SOAP note.

## Configuration

### Medical Keyterms

Customize the `MEDICAL_KEYTERMS` list in `medical_scribe.py` to boost recognition for your clinical specialty:

```python
MEDICAL_KEYTERMS = [
    # Conditions
    "hypertension", "diabetes mellitus", "coronary artery disease",
    # Medications with dosages
    "metformin 1000mg", "lisinopril 10mg", "atorvastatin 20mg",
    # Clinical terms
    "chief complaint", "history of present illness", "review of systems",
    "auscultation", "palpation", "echocardiogram",
    # Vitals
    "hemoglobin A1c", "blood pressure", "oxygen saturation",
]
```

### Turn Detection

The VAD (Voice Activity Detection) is configured for clinical conversations where speakers naturally pause longer:

| Parameter | Value | Purpose |
|-----------|-------|---------|
| `stop_secs` | 0.8s | Wait 800ms of silence before finalizing a turn |
| `confidence` | 0.6 | Speech detection confidence threshold |
| `min_volume` | 0.4 | Minimum volume threshold to detect speech |

### LLM Gateway Model

Set `LLM_GATEWAY_MODEL` in `.env` to change the model for post-processing. Default: `claude-haiku-4-5-20251001`

## Production Considerations

### Hybrid Streaming + Async Approach

For production deployments, consider a hybrid approach:

| Phase | Method | Features |
|-------|--------|----------|
| **During visit** | Streaming (this app) | Real-time transcription, immediate documentation |
| **Post-visit** | AssemblyAI Async API | Speaker diarization, PII redaction, compliance-ready output |

Speaker diarization and PII redaction are async-only features. The hybrid approach gives clinicians immediate access to documentation while ensuring the final record is speaker-attributed and HIPAA-compliant.

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `ASSEMBLYAI_API_KEY` | Yes | Your AssemblyAI API key |
| `LLM_GATEWAY_MODEL` | No | Model for post-processing (default: `claude-haiku-4-5-20251001`) |

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/soap` | POST | Generate SOAP note from current encounter |
| `/api/reset` | POST | Clear the encounter buffer for a new session |

## Project Structure

```
medical-scribe/pipecat/
├── medical_scribe.py   # Main pipeline: STT, LLM processing, SOAP generation
├── run.py              # Server runner with WebSocket broadcasting
├── requirements.txt    # Python dependencies
├── .env.example        # Environment template
└── client/             # Next.js frontend
    ├── src/
    └── package.json
```
