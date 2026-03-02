# Medical Scribe — Pipecat + AssemblyAI Universal-3 Pro

A real-time ambient clinical documentation agent using Pipecat's pipeline framework and AssemblyAI's Universal-3 Pro streaming speech-to-text. The agent listens to a doctor-patient conversation, transcribes in real time, post-processes each turn through AssemblyAI's LLM Gateway for medical terminology correction, and generates a SOAP note at session end. This is a **passive, listen-only scribe** — no TTS output.

## Architecture

```
Microphone → AssemblyAI STT (U3P) → LLM Gateway (per-turn editing) → Client UI
                                                                     ↓
                                                              SOAP note (on disconnect)
```

- **STT**: AssemblyAI Universal-3 Pro (`u3-rt-pro`) — sub-300ms streaming transcription with conservative turn detection tuned for clinical pauses
- **LLM Gateway**: AssemblyAI LLM Gateway (`claude-3-5-haiku`) — per-turn medical terminology correction + SOAP note generation
- **Transport**: WebRTC via Pipecat (audio input only, no output)
- **Client**: Custom Next.js frontend showing live transcript and SOAP notes

## Prerequisites

- Python 3.10+
- Node.js 18+
- An AssemblyAI API key (used for both STT and LLM Gateway)

## Setup

### Server

1. Navigate to this directory:

```bash
cd medical-scribe/pipecat
```

2. Create a virtual environment and install dependencies:

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

3. Copy `.env.example` to `.env` and add your API key:

```bash
cp .env.example .env
```

### Client

1. Navigate to the client directory and install dependencies:

```bash
cd client
npm install
```

2. Copy `.env.example` to `.env.local`:

```bash
cp .env.example .env.local
```

## Running

Start the server (from the `medical-scribe/pipecat` directory):

```bash
python medical_scribe.py
```

In a separate terminal, start the client (from `medical-scribe/pipecat/client`):

```bash
npm run dev
```

Open `http://localhost:3000` in your browser. Click **Start Recording** and begin the clinical encounter. The transcript updates in real time. When you click **End Session**, a SOAP note is auto-generated.

## Key Features

- **Listen-only pipeline**: No TTS or LLM response — the agent passively transcribes without interrupting the clinical workflow
- **Conservative turn detection**: `min_end_of_turn_silence_when_confident` set to 800ms and `max_turn_silence` set to 3600ms, tuned for natural clinical pauses where doctors and patients take longer breaks between phrases
- **Medical keyterms boosting**: Domain-specific terms (medications, conditions, exam procedures) are boosted for higher transcription accuracy
- **Per-turn LLM editing**: Each finalized transcript turn is post-processed through AssemblyAI's LLM Gateway to correct medical terminology, drug names, dosages, and anatomy terms
- **SOAP note generation**: Full encounter transcript is sent to the LLM Gateway on session end to produce a structured Subjective/Objective/Assessment/Plan note
- **Real-time client**: Custom Next.js frontend with live transcript display, interim (partial) transcripts, and SOAP note panel

## Configuration

### Turn Detection

Adjust turn detection timing in the `AssemblyAIConnectionParams`:

| Parameter | Value | Description |
|---|---|---|
| `min_end_of_turn_silence_when_confident` | 800ms | Conservative — allows clinical pauses without prematurely ending turns |
| `max_turn_silence` | 3600ms | Long timeout for extended clinical silences (e.g., during physical examination) |

### Medical Keyterms

Update the `MEDICAL_KEYTERMS` list to boost recognition of terminology specific to your clinical specialty:

```python
MEDICAL_KEYTERMS = [
    "hypertension", "diabetes mellitus", "coronary artery disease",
    "metformin 1000mg", "lisinopril 10mg", "atorvastatin 20mg",
    ...
]
```

### LLM Gateway Model

Set `LLM_GATEWAY_MODEL` in `.env` to change the model used for post-processing. Default is `claude-haiku-4-5-20251001`.

## Hybrid Approach: Streaming + Async

Speaker diarization and PII redaction are **async-only** features in AssemblyAI. The recommended production approach is:

1. **During the visit**: Stream audio for real-time transcription and documentation (this app)
2. **Post-visit**: Process the recording through AssemblyAI's async API for speaker-labeled, PII-redacted SOAP notes

This gives clinicians immediate access to documentation while ensuring the final record is speaker-attributed and compliant.
