"use client";

import { useState, useCallback, useRef, useEffect } from "react";

const BACKEND_URL =
  process.env.NEXT_PUBLIC_BACKEND_URL || "http://localhost:7860";

/* ================================================================
   Pre-connection screen
   ================================================================ */

export default function Page() {
  const [isConnected, setIsConnected] = useState(false);
  const [isConnecting, setIsConnecting] = useState(false);

  // WebRTC + WebSocket refs
  const pcRef = useRef<RTCPeerConnection | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const streamRef = useRef<MediaStream | null>(null);

  // Transcript + SOAP state lifted here so it persists across phases
  const [transcripts, setTranscripts] = useState<EditedTranscript[]>([]);
  const [soapNote, setSoapNote] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);

  const connect = useCallback(async () => {
    setIsConnecting(true);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, sampleRate: 16000 },
      });
      streamRef.current = stream;

      // WebSocket for transcripts
      const wsProtocol = BACKEND_URL.startsWith("https") ? "wss" : "ws";
      const ws = new WebSocket(
        BACKEND_URL.replace(/^https?/, wsProtocol) + "/ws/transcripts"
      );
      wsRef.current = ws;

      ws.onmessage = (event) => {
        const data = JSON.parse(event.data);
        if (data.type === "transcript") {
          setTranscripts((prev) => [...prev, data]);
        } else if (data.type === "soap_note") {
          setSoapNote(data.content);
          setStatus(null);
        } else if (data.type === "status") {
          setStatus(data.message);
        }
      };

      ws.onclose = () => {
        console.log("WebSocket closed");
      };

      // WebRTC peer connection
      const pc = new RTCPeerConnection({
        iceServers: [{ urls: "stun:stun.l.google.com:19302" }],
      });
      pcRef.current = pc;
      stream.getAudioTracks().forEach((track) => pc.addTrack(track, stream));

      const startRes = await fetch(`${BACKEND_URL}/start`, { method: "POST" });
      const { session_id } = await startRes.json();

      const offer = await pc.createOffer();
      await pc.setLocalDescription(offer);

      const offerRes = await fetch(
        `${BACKEND_URL}/sessions/${session_id}/api/offer`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ sdp: offer.sdp, type: offer.type }),
        }
      );
      const answer = await offerRes.json();
      await pc.setRemoteDescription(
        new RTCSessionDescription({ sdp: answer.sdp, type: answer.type })
      );

      // ICE candidates
      const candidates: RTCIceCandidateInit[] = [];
      let gatheringDone = false;
      const sendCandidates = () => {
        if (candidates.length > 0) {
          fetch(`${BACKEND_URL}/sessions/${session_id}/api/offer`, {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ pc_id: answer.pc_id, candidates }),
          });
        }
      };
      pc.onicecandidate = (e) => {
        if (e.candidate) candidates.push(e.candidate.toJSON());
      };
      pc.onicegatheringstatechange = () => {
        if (pc.iceGatheringState === "complete" && !gatheringDone) {
          gatheringDone = true;
          sendCandidates();
        }
      };
      if (pc.iceGatheringState === "complete") {
        gatheringDone = true;
        sendCandidates();
      }

      pc.onconnectionstatechange = () => {
        if (pc.connectionState === "connected") {
          setIsConnected(true);
          setIsConnecting(false);
        }
      };
    } catch (err) {
      console.error("Connection error:", err);
      setIsConnecting(false);
    }
  }, []);

  const disconnect = useCallback(() => {
    pcRef.current?.close();
    pcRef.current = null;
    wsRef.current?.close();
    wsRef.current = null;
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
    setIsConnected(false);
    setIsConnecting(false);
    setTranscripts([]);
    setSoapNote(null);
    setStatus(null);
  }, []);

  const requestSoapNote = useCallback(async () => {
    setStatus("Generating SOAP note...");
    try {
      const res = await fetch(`${BACKEND_URL}/api/soap`, { method: "POST" });
      const data = await res.json();
      setSoapNote(data.soap_note);
      setStatus(null);
    } catch (err) {
      console.error("SOAP generation error:", err);
      setStatus(null);
    }
  }, []);

  if (!isConnected) {
    return (
      <div className="flex flex-col items-center justify-center min-h-screen gap-8">
        <div className="scribe-icon">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <path d="M12 18.5a6.5 6.5 0 0 0 6.5-6.5V6a6.5 6.5 0 0 0-13 0v6a6.5 6.5 0 0 0 6.5 6.5Z" />
            <path d="M12 18.5V22" />
            <path d="M8 22h8" />
          </svg>
        </div>
        <div className="flex flex-col items-center gap-2">
          <h1 className="text-2xl font-semibold tracking-tight">Medical Scribe</h1>
          <p className="text-zinc-500 text-sm text-center max-w-xs">
            Ambient clinical documentation powered by Universal-3 Pro
          </p>
        </div>
        <button
          onClick={connect}
          disabled={isConnecting}
          className="px-10 py-3 bg-emerald-600 text-white rounded-full font-medium text-base hover:bg-emerald-500 transition-colors cursor-pointer disabled:opacity-50"
        >
          {isConnecting ? "Connecting..." : "Start Encounter"}
        </button>
      </div>
    );
  }

  return (
    <MedicalScribeView
      transcripts={transcripts}
      soapNote={soapNote}
      status={status}
      requestSoapNote={requestSoapNote}
      disconnect={disconnect}
      streamRef={streamRef}
    />
  );
}

/* ================================================================
   Encounter timer hook
   ================================================================ */

function useEncounterTimer() {
  const [elapsed, setElapsed] = useState(0);
  const startRef = useRef(Date.now());

  useEffect(() => {
    const interval = setInterval(() => {
      setElapsed(Math.floor((Date.now() - startRef.current) / 1000));
    }, 1000);
    return () => clearInterval(interval);
  }, []);

  const hrs = Math.floor(elapsed / 3600);
  const mins = Math.floor((elapsed % 3600) / 60);
  const secs = elapsed % 60;
  return hrs > 0
    ? `${hrs}:${String(mins).padStart(2, "0")}:${String(secs).padStart(2, "0")}`
    : `${String(mins).padStart(2, "0")}:${String(secs).padStart(2, "0")}`;
}

/* ================================================================
   Types
   ================================================================ */

interface EditedTranscript {
  text: string;
  original: string;
  timestamp: string;
}

/* ================================================================
   Main encounter view
   ================================================================ */

function MedicalScribeView({
  transcripts,
  soapNote,
  status,
  requestSoapNote,
  disconnect,
  streamRef,
}: {
  transcripts: EditedTranscript[];
  soapNote: string | null;
  status: string | null;
  requestSoapNote: () => void;
  disconnect: () => void;
  streamRef: React.RefObject<MediaStream | null>;
}) {
  const timer = useEncounterTimer();
  const [phase, setPhase] = useState<"recording" | "review">("recording");

  const endEncounter = useCallback(() => {
    streamRef.current?.getAudioTracks().forEach((t) => (t.enabled = false));
    setPhase("review");
  }, [streamRef]);

  const isRecording = phase === "recording";

  return (
    <div className="flex flex-col h-screen">
      {/* ── Header ────────────────────────────────────── */}
      <header className="flex items-center justify-between px-6 py-4 border-b border-zinc-800/60">
        <div className="flex items-center gap-3">
          {isRecording ? (
            <>
              <div className="recording-dot" />
              <span className="text-sm font-medium text-red-400 uppercase tracking-wider">
                Recording
              </span>
            </>
          ) : (
            <>
              <div className="w-3 h-3 rounded-full bg-amber-500 shrink-0" />
              <span className="text-sm font-medium text-amber-400 uppercase tracking-wider">
                Review
              </span>
            </>
          )}
        </div>
        <h1 className="text-lg font-semibold">Medical Scribe</h1>
        <div className="flex items-center gap-2 text-zinc-400 text-sm font-mono">
          <ClockIcon />
          {timer}
        </div>
      </header>

      {/* ── Main content: transcript + SOAP note ─────── */}
      <div className="flex-1 flex flex-col lg:flex-row overflow-hidden">
        {/* Transcript panel */}
        <div className="flex-1 flex flex-col border-b lg:border-b-0 lg:border-r border-zinc-800/60 min-h-0">
          <div className="px-6 py-3 border-b border-zinc-800/40">
            <h2 className="text-sm font-semibold text-zinc-400 uppercase tracking-wider">
              {isRecording ? "Live Transcript" : "Encounter Transcript"}
            </h2>
          </div>
          <TranscriptPanel transcripts={transcripts} />
        </div>

        {/* SOAP note panel */}
        <div className="flex-1 flex flex-col min-h-0 lg:max-w-[50%]">
          <div className="px-6 py-3 border-b border-zinc-800/40 flex items-center justify-between">
            <h2 className="text-sm font-semibold text-zinc-400 uppercase tracking-wider">
              SOAP Note
            </h2>
            {!isRecording && (
              <button
                onClick={requestSoapNote}
                disabled={!!status || !!soapNote}
                className="px-4 py-1.5 bg-emerald-600 hover:bg-emerald-500 disabled:bg-zinc-700 disabled:text-zinc-500 text-white text-xs font-medium rounded-full transition-colors cursor-pointer disabled:cursor-not-allowed"
              >
                {status || (soapNote ? "Generated" : "Generate")}
              </button>
            )}
          </div>
          <SoapNotePanel
            soapNote={soapNote}
            status={status}
            phase={phase}
            requestSoapNote={requestSoapNote}
          />
        </div>
      </div>

      {/* ── Footer controls ──────────────────────────── */}
      <footer className="flex items-center justify-center gap-4 px-6 py-4 border-t border-zinc-800/60">
        {isRecording ? (
          <button
            onClick={endEncounter}
            className="px-6 py-2 bg-amber-600 hover:bg-amber-500 text-white rounded-full font-medium transition-colors cursor-pointer text-sm"
          >
            End Encounter
          </button>
        ) : (
          <button
            onClick={disconnect}
            className="px-6 py-2 bg-red-600 hover:bg-red-500 text-white rounded-full font-medium transition-colors cursor-pointer text-sm"
          >
            Close Session
          </button>
        )}
      </footer>
    </div>
  );
}

/* ================================================================
   Transcript panel — shows raw text + edited versions
   ================================================================ */

function TranscriptPanel({ transcripts }: { transcripts: EditedTranscript[] }) {
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [transcripts]);

  if (transcripts.length === 0) {
    return (
      <div className="flex-1 flex items-center justify-center px-6">
        <div className="max-w-sm">
          <div className="waveform mx-auto mb-6 justify-center">
            {[...Array(5)].map((_, i) => (
              <div
                key={i}
                className="waveform-bar"
                style={{
                  height: `${12 + Math.sin(i * 1.2) * 10}px`,
                  animationDelay: `${i * 0.1}s`,
                }}
              />
            ))}
          </div>
          <h3 className="text-zinc-300 text-sm font-semibold mb-3 text-center">
            Ambient Scribe Active
          </h3>
          <p className="text-zinc-500 text-sm leading-relaxed text-center mb-4">
            This scribe is listening to the clinical encounter and transcribing
            in real time. Speak naturally — it captures everything automatically.
          </p>
          <div className="space-y-2 text-xs text-zinc-600">
            <div className="flex items-start gap-2">
              <span className="text-emerald-500 mt-0.5">1.</span>
              <span>Conversation is transcribed live as you speak</span>
            </div>
            <div className="flex items-start gap-2">
              <span className="text-emerald-500 mt-0.5">2.</span>
              <span>Click <strong className="text-zinc-400">End Encounter</strong> when finished</span>
            </div>
            <div className="flex items-start gap-2">
              <span className="text-emerald-500 mt-0.5">3.</span>
              <span>Generate a structured SOAP note from the transcript</span>
            </div>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div
      ref={scrollRef}
      className="flex-1 overflow-y-auto px-6 py-4 flex flex-col gap-3 scrollbar-thin"
    >
      {transcripts.map((entry, i) => (
        <div key={i} className="flex gap-3">
          <span className="text-zinc-600 text-xs font-mono mt-0.5 shrink-0 w-14">
            {entry.timestamp}
          </span>
          <div className="flex-1">
            <p className="text-sm text-zinc-200 leading-relaxed">
              {entry.original}
            </p>
            {entry.text !== entry.original && (
              <p className="text-xs text-emerald-400/70 mt-1 leading-relaxed">
                {entry.text}
              </p>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}

/* ================================================================
   SOAP note panel
   ================================================================ */

function SoapNotePanel({
  soapNote,
  status,
  phase,
  requestSoapNote,
}: {
  soapNote: string | null;
  status: string | null;
  phase: "recording" | "review";
  requestSoapNote: () => void;
}) {
  if (status && !soapNote) {
    return (
      <div className="flex-1 flex items-center justify-center px-6">
        <div className="text-center">
          <div className="inline-block h-6 w-6 animate-spin rounded-full border-2 border-emerald-500 border-t-transparent mb-3" />
          <p className="text-zinc-400 text-sm">{status}</p>
        </div>
      </div>
    );
  }

  if (!soapNote) {
    return (
      <div className="flex-1 flex items-center justify-center px-6">
        <div className="text-center max-w-xs">
          <NoteIcon />
          {phase === "review" ? (
            <>
              <p className="text-zinc-400 text-sm mt-3">
                Encounter ended. Ready to generate your SOAP note.
              </p>
              <button
                onClick={requestSoapNote}
                className="mt-4 px-8 py-3 bg-emerald-600 hover:bg-emerald-500 text-white font-medium rounded-full transition-colors cursor-pointer"
              >
                Generate SOAP Note
              </button>
            </>
          ) : (
            <>
              <p className="text-zinc-500 text-sm mt-3">
                SOAP note will be generated from the encounter transcript
              </p>
              <p className="text-zinc-600 text-xs mt-1">
                End the encounter first, then generate
              </p>
            </>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="flex-1 overflow-y-auto px-6 py-4 scrollbar-thin">
      <div className="soap-section prose prose-sm prose-invert max-w-none">
        <div className="soap-content text-sm text-zinc-300 whitespace-pre-wrap leading-relaxed">
          {soapNote}
        </div>
      </div>
    </div>
  );
}

/* ================================================================
   Small UI components
   ================================================================ */

function ClockIcon() {
  return (
    <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="10" />
      <polyline points="12 6 12 12 16 14" />
    </svg>
  );
}

function NoteIcon() {
  return (
    <svg className="w-10 h-10 text-zinc-600 mx-auto" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M14.5 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7.5L14.5 2z" />
      <polyline points="14 2 14 8 20 8" />
      <line x1="16" y1="13" x2="8" y2="13" />
      <line x1="16" y1="17" x2="8" y2="17" />
      <line x1="10" y1="9" x2="8" y2="9" />
    </svg>
  );
}
