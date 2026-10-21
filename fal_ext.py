import os
import threading
import queue
import asyncio
import base64


def load_env(filename=".env"):
    """
    Minimal .env loader (no external dependencies).
    Loads KEY=VALUE pairs into os.environ. Existing environment
    variables take precedence and are never overwritten.
    Search order (first file found wins):
      1. Folder containing this file (project root)
      2. TouchDesigner project folder (when running inside TD)
      3. Current working directory
    Returns the path of the loaded file, or None.
    """
    candidates = []
    try:
        candidates.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), filename))
    except NameError:
        pass  # __file__ undefined when exec'd from a DAT inside TD
    try:
        import td
        candidates.append(os.path.join(td.project.folder, filename))
    except Exception:
        pass
    candidates.append(os.path.join(os.getcwd(), filename))

    env_path = next((p for p in candidates if os.path.isfile(p)), None)
    if not env_path:
        return None

    with open(env_path, "r", encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key:
                os.environ.setdefault(key, value)
    return env_path


# Load credentials (FAL_KEY) before fal_client is used.
# fal_client resolves credentials lazily at connection time, so this is enough.
load_env()

import fal_client

try:
    import numpy as np
    import cv2
    import av
    from aiortc import (
        RTCPeerConnection,
        RTCConfiguration,
        RTCIceServer,
        RTCSessionDescription,
        VideoStreamTrack,
    )
    from aiortc.sdp import candidate_from_sdp
except ImportError as e:
    raise ImportError(
        f"FalExt: missing dependency ({e}). "
        "Install aiortc, av, opencv-python and numpy into the project venv."
    )


# NOTE: pass the APP id without the realtime suffix — fal_client appends
# "/realtime" itself when building the WebSocket URL.
APP_ID = "decart/lucy-2-5"      # or "decart/lucy2-vton" for the try-on-only model
SEND_PERSON_IN_INPUT = False    # True for lucy2-vton; lucy-2-5 ignores image_url in WebRTC mode
PROMPT_EXPANSION = True         # lucy-2-5: let Decart expand/enhance the prompt
DEFAULT_ICE_SERVERS = [{"urls": "stun:stun.l.google.com:19302"}]
NEGOTIATION_TIMEOUT = 30.0      # seconds to wait for the SDP answer
ICE_GRACE_PERIOD = 1.0          # seconds to wait for a separate iceservers message
RECONNECT_DELAY = 3.0           # base seconds between connection attempts
MAX_RECONNECT_DELAY = 60.0      # cap for exponential backoff
SESSION_LIMIT_DELAY = 30.0      # wait after "Concurrent session limit" errors
TOKEN_EXPIRATION = 3600         # JWT lifetime for the signaling connection
JPEG_QUALITY = 85               # quality of the received frames handed to TD
AUTO_START = False              # True: connect on extension init. False: wait for start()
DEBUG = False                   # True: log every signaling message


class _RestartRequested(Exception):
    """Raised internally when the session must restart (e.g. prompt changed)."""


class _LatestFrameTrack(VideoStreamTrack):
    """
    WebRTC video track that continuously re-transmits the latest frame
    supplied by TouchDesigner at ~30 fps (aiortc's next_timestamp pacing).
    """

    def __init__(self, get_frame):
        super().__init__()
        self._get_frame = get_frame

    async def recv(self):
        pts, time_base = await self.next_timestamp()
        frame = av.VideoFrame.from_ndarray(self._get_frame(), format="rgb24")
        frame.pts = pts
        frame.time_base = time_base
        return frame


class FalExt:
    """
    TouchDesigner Extension for the fal.ai Lucy realtime (WebRTC) API.

    TD-facing interface (unchanged from the WebSocket version):
      - send_frame(payload): {"image_url": person data URI,
                              "reference_image_url": garment data URI,
                              "prompt": optional text}
      - get_latest_frame():  JPEG bytes of the latest processed frame
      - stop()

    Internals: the fal WebSocket is used ONLY for WebRTC signaling
    (offer/answer/ICE) and model control messages. Person frames flow to the
    model over a WebRTC video track; processed frames arrive on the remote
    track and are re-encoded to JPEG for TD.
    """

    def __init__(self, ownerComp):
        self.ownerComp = ownerComp
        self.send_queue = queue.Queue(maxsize=10)
        self.latest_image_bytes = None
        self.is_running = False
        self.loop = None
        self.connection = None
        self.worker_thread = None
        self._stop_event = None
        self._restart_requested = False
        self._start_lock = threading.Lock()

        # Latest input state (shared with the worker thread via GIL-atomic refs)
        self._input_frame = np.zeros((512, 512, 3), dtype=np.uint8)
        self._person_uri = None
        self._control = {}
        self._remote_track = None
        self._pc = None
        self._session_live = False

        # Stop any orphaned connections from previous compilations
        import sys
        if not hasattr(sys, 'fal_connections'):
            sys.fal_connections = []

        for old_ext in list(sys.fal_connections):
            try:
                old_ext.stop()
            except Exception as e:
                print(f"FalExt: Error stopping old extension: {e}")

        sys.fal_connections.clear()
        sys.fal_connections.append(self)

        if AUTO_START:
            self.start()

    # ------------------------------------------------------------------
    # TouchDesigner-facing API
    # ------------------------------------------------------------------

    def send_frame(self, payload):
        """Accept a payload from TD. Person frames feed the WebRTC video
        track; garment/prompt are forwarded as control messages when changed."""
        person_uri = payload.get("image_url")
        if person_uri and person_uri.startswith("data:image"):
            try:
                raw = base64.b64decode(person_uri.split(",", 1)[1])
                img = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
                if img is not None:
                    self._input_frame = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
                    self._person_uri = person_uri
            except Exception as e:
                print(f"FalExt: could not decode person frame: {e}")
        elif person_uri:
            self._person_uri = person_uri

        control = {}
        if payload.get("reference_image_url"):
            control["reference_image_url"] = payload["reference_image_url"]
        if payload.get("prompt"):
            control["prompt"] = payload["prompt"]

        if control and control != self._control:
            prompt_changed = control.get("prompt") != self._control.get("prompt")
            self._control = control
            if prompt_changed and self.is_active():
                # The model only reads the prompt when a generation starts, and a
                # running generation ignores mid-session prompt updates. Restart the
                # session so the new prompt is used as the initial input.
                print("FalExt: prompt changed - restarting session to apply it")
                self._restart_requested = True
            else:
                # Reference-only update: the running generation accepts it (set_image)
                if self.send_queue.full():
                    try:
                        self.send_queue.get_nowait()
                    except queue.Empty:
                        pass
                self.send_queue.put(control)

    def get_latest_frame(self):
        """Retrieve the latest processed frame as JPEG bytes."""
        return self.latest_image_bytes

    def start(self):
        """Starts the connection. Safe to call when already running."""
        with self._start_lock:
            if self.worker_thread and self.worker_thread.is_alive():
                print("FalExt: already running")
                return
            self.is_running = True
            self._session_live = False
            self._remote_track = None
            self._restart_requested = False
            self.latest_image_bytes = None  # drop stale frames from a previous run
            self.worker_thread = threading.Thread(target=self._run_async_loop, daemon=True)
            self.worker_thread.start()
            print("FalExt: starting...")

    def stop(self):
        """Stops the connection. The extension can be restarted with start()."""
        if not self.is_running:
            return
        print("FalExt: stopping...")
        self.is_running = False
        loop = self.loop
        if loop and loop.is_running():
            try:
                # Wake the reconnect backoff sleep, then close signaling + peer.
                # The coroutine is created inside the loop callback so a loop
                # that closes first never leaves an un-awaited coroutine behind.
                if self._stop_event is not None:
                    loop.call_soon_threadsafe(self._stop_event.set)
                loop.call_soon_threadsafe(
                    lambda: asyncio.ensure_future(self._shutdown())
                )
            except Exception:
                pass

    def is_active(self):
        """True when the worker thread is alive."""
        return self.worker_thread is not None and self.worker_thread.is_alive()

    def get_status(self):
        """'live' | 'connecting' | 'stopped' - handy for a TD status display."""
        if not self.is_active():
            return "stopped"
        return "live" if self._session_live else "connecting"

    def onDestroy(self):
        """Called automatically by TouchDesigner when the extension is destroyed/recompiled."""
        self.stop()

    def __del__(self):
        self.stop()

    def _get_input_frame(self):
        return self._input_frame

    async def _shutdown(self):
        try:
            if self.connection:
                await self.connection.close()
        except Exception:
            pass
        try:
            if self._pc:
                await self._pc.close()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Background worker
    # ------------------------------------------------------------------

    def _run_async_loop(self):
        """Entry point for the background thread."""
        loop = asyncio.new_event_loop()
        self.loop = loop
        self._stop_event = asyncio.Event()
        try:
            asyncio.set_event_loop(loop)
            loop.run_until_complete(self._async_worker())
        except Exception as e:
            print(f"FalExt Thread Error: {e}")
        finally:
            loop.close()
            # Only clear if no new worker has already taken over
            if self.loop is loop:
                self.loop = None
            if threading.current_thread() is self.worker_thread:
                self.worker_thread = None
            print("FalExt: stopped")

    async def _async_worker(self):
        """Reconnect loop with exponential backoff. Decart enforces a low
        concurrent-session limit, so retries after a session-limit error
        wait much longer to let stale server-side sessions expire."""
        delay = RECONNECT_DELAY
        while self.is_running:
            self._session_live = False
            try:
                await self._run_session()
            except _RestartRequested:
                if self.is_running:
                    print("FalExt: restarting session")
            except Exception as e:
                if self.is_running:
                    msg = str(e)
                    print(f"FalExt: session ended ({msg})")
                    if "Concurrent session limit" in msg:
                        delay = SESSION_LIMIT_DELAY
            self._remote_track = None
            if self._pc:
                try:
                    await self._pc.close()
                except Exception:
                    pass
                self._pc = None
            if not self.is_running:
                break
            if self._session_live:
                delay = RECONNECT_DELAY       # healthy session: quick retry
            elif delay != SESSION_LIMIT_DELAY:
                delay = min(delay * 2, MAX_RECONNECT_DELAY)
            print(f"FalExt: reconnecting in {delay:.0f}s...")
            try:
                # Interruptible sleep: stop() sets the event to wake us at once
                await asyncio.wait_for(self._stop_event.wait(), timeout=delay)
                break  # stop requested
            except asyncio.TimeoutError:
                pass

    async def _run_session(self):
        """One full session: signaling + WebRTC negotiation + media."""
        # Fresh AsyncClient per session: the module-level fal_client.realtime_async
        # reuses a singleton whose httpx client binds to the FIRST event loop it
        # ran on, which breaks every restart with "Event loop is closed".
        client = fal_client.AsyncClient()
        async with client.realtime(
            APP_ID, token_expiration=TOKEN_EXPIRATION
        ) as connection:
            self.connection = connection
            print(f"FalExt: signaling connected to {APP_ID}/realtime")

            negotiated = asyncio.Event()
            state = {"pc": None, "remote_ok": False, "pending": []}
            grace_task = None

            async def start_peer(ice_servers=None):
                if state["pc"] is not None:
                    return
                servers = [_to_rtc_ice_server(s) for s in (ice_servers or DEFAULT_ICE_SERVERS)]
                pc = RTCPeerConnection(RTCConfiguration(iceServers=servers))
                state["pc"] = pc
                self._pc = pc

                # Outgoing video: latest TD frame at ~30fps
                pc.addTrack(_LatestFrameTrack(self._get_input_frame))

                @pc.on("track")
                def on_track(track):
                    if track.kind == "video":
                        self._remote_track = track

                # aiortc gathers ICE candidates during setLocalDescription,
                # so the offer SDP already contains them.
                offer = await pc.createOffer()
                await pc.setLocalDescription(offer)
                await connection.send({"type": "offer", "sdp": pc.localDescription.sdp})
                if DEBUG:
                    print("FalExt SIG >> offer sent")

            async def grace_start():
                await asyncio.sleep(ICE_GRACE_PERIOD)
                await start_peer()

            async def signaling_recv():
                nonlocal grace_task
                while self.is_running:
                    msg = await connection.recv()
                    if DEBUG:
                        print(f"FalExt SIG << {str(msg)[:300]}")
                    if not isinstance(msg, dict):
                        continue
                    mtype = str(msg.get("type", "")).lower()

                    if mtype == "ready":
                        servers = _extract_ice_servers(msg)
                        if servers:
                            await start_peer(servers)
                        else:
                            grace_task = asyncio.create_task(grace_start())
                    elif mtype == "iceservers":
                        if grace_task:
                            grace_task.cancel()
                        await start_peer(_extract_ice_servers(msg))
                    elif mtype == "answer":
                        pc = state["pc"]
                        if pc and msg.get("sdp") and not state["remote_ok"]:
                            await pc.setRemoteDescription(
                                RTCSessionDescription(sdp=msg["sdp"], type="answer")
                            )
                            state["remote_ok"] = True
                            for cand in state["pending"]:
                                await pc.addIceCandidate(cand)
                            state["pending"].clear()
                            negotiated.set()
                            self._session_live = True
                            print("FalExt: WebRTC negotiation completed")
                    elif mtype == "icecandidate":
                        cand = _build_ice_candidate(msg.get("candidate"))
                        if cand is not None:
                            if state["pc"] and state["remote_ok"]:
                                await state["pc"].addIceCandidate(cand)
                            else:
                                state["pending"].append(cand)
                    elif mtype == "set_image_ack":
                        # Acknowledgement of a control update; error is None on success
                        if not msg.get("success"):
                            print(f"FalExt: control update rejected: {msg}")
                    elif mtype in ("x-fal-error", "error"):
                        raise RuntimeError(f"endpoint reported error: {msg}")
                    elif msg.get("error") and not msg.get("type"):
                        # Plain error payload, e.g. {'error': 'Error while forwarding...'}
                        raise RuntimeError(f"endpoint reported error: {msg}")

            async def control_sender():
                while self.is_running:
                    try:
                        msg = self.send_queue.get_nowait()
                    except queue.Empty:
                        await asyncio.sleep(0.01)
                        continue
                    await connection.send(msg)
                    if DEBUG:
                        print(f"FalExt SIG >> control {_summarize(msg)}")

            async def rtc_reader():
                while self.is_running:
                    track = self._remote_track
                    if track is None:
                        await asyncio.sleep(0.05)
                        continue
                    frame = await track.recv()
                    img = frame.to_ndarray(format="rgb24")
                    ok, jpg = cv2.imencode(
                        ".jpg",
                        cv2.cvtColor(img, cv2.COLOR_RGB2BGR),
                        [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY],
                    )
                    if ok:
                        self.latest_image_bytes = jpg.tobytes()

            async def negotiation_watchdog():
                try:
                    await asyncio.wait_for(negotiated.wait(), timeout=NEGOTIATION_TIMEOUT)
                except asyncio.TimeoutError:
                    raise RuntimeError("WebRTC negotiation timed out") from None

            async def restart_watcher():
                while self.is_running:
                    if self._restart_requested:
                        self._restart_requested = False
                        raise _RestartRequested()
                    await asyncio.sleep(0.25)

            # The first model input starts the session on the worker (and the
            # generation, which is where the prompt is read).
            initial = dict(self._control)
            if SEND_PERSON_IN_INPUT and self._person_uri:
                initial["image_url"] = self._person_uri
            initial["enable_prompt_expansion"] = PROMPT_EXPANSION
            await connection.send(initial)
            if DEBUG:
                print(f"FalExt SIG >> initial input {_summarize(initial)}")

            # First exception (e.g. negotiation timeout, endpoint error) ends the
            # session; siblings are cancelled so the failure propagates at once.
            # A manual stop() surfaces as connection errors after is_running is
            # already False, so it exits quietly instead of triggering reconnects.
            tasks = [
                asyncio.create_task(t)
                for t in (signaling_recv(), control_sender(), rtc_reader(),
                          negotiation_watchdog(), restart_watcher())
            ]
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
            for t in pending:
                t.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            if self.is_running:
                for t in done:
                    if not t.cancelled() and t.exception() is not None:
                        raise t.exception()


# ----------------------------------------------------------------------
# Signaling helpers
# ----------------------------------------------------------------------

def _summarize(msg):
    """Short, log-safe view of an outgoing message (truncates data URIs)."""
    out = {}
    for k, v in msg.items():
        s = str(v)
        out[k] = s if len(s) <= 80 else s[:60] + f"...<{len(s)} chars>"
    return out


def _extract_ice_servers(msg):
    return msg.get("iceServers") or msg.get("ice_servers") or msg.get("iceservers")


def _to_rtc_ice_server(server):
    if isinstance(server, str):
        return RTCIceServer(urls=server)
    return RTCIceServer(
        urls=server.get("urls"),
        username=server.get("username"),
        credential=server.get("credential"),
    )


def _build_ice_candidate(data):
    if not data or not data.get("candidate"):
        return None
    raw = data["candidate"]
    if raw.startswith("candidate:"):
        raw = raw.split(":", 1)[1]
    cand = candidate_from_sdp(raw)
    cand.sdpMid = data.get("sdpMid")
    cand.sdpMLineIndex = data.get("sdpMLineIndex")
    return cand
