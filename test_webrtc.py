"""
End-to-end test of the Lucy WebRTC pipeline (runs outside TouchDesigner).

Feeds the test image as the video source, waits for processed frames,
and saves the first few received frames to disk as webrtc_out_XXX.jpg.
"""
import base64
import time

from fal_ext import FalExt, load_env


class MockCOMP:
    path = "/mock"


if __name__ == "__main__":
    load_env()

    with open("739784TOVA91055_F.jpg", "rb") as f:
        uri = "data:image/jpeg;base64," + base64.b64encode(f.read()).decode()

    payload = {
        "image_url": uri,
        "reference_image_url": uri,
        "prompt": "Substitute the current top with the outfit from the reference image",
    }

    ext = FalExt(MockCOMP())
    ext.start()

    received = 0
    last = None
    start = time.time()
    duration = 120  # seconds

    print("Waiting for processed frames (cold start can take 20-40s)...")
    while time.time() - start < duration:
        ext.send_frame(payload)  # keep the input fed, like the TD trigger would
        time.sleep(1.0)
        img = ext.get_latest_frame()
        if img and img != last:
            received += 1
            last = img
            fname = f"webrtc_out_{received:03d}.jpg"
            with open(fname, "wb") as f:
                f.write(img)
            print(f"[{time.time() - start:5.1f}s] received frame {received} "
                  f"({len(img)} bytes) -> {fname}")
            if received >= 5:
                break

    ext.stop()
    time.sleep(2)  # let the WebSocket close cleanly so the Decart session is released
    print(f"Done. Received {received} frame(s) in {time.time() - start:.1f}s")
