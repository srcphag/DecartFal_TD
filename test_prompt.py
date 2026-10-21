"""
Verifies that changing the prompt restarts the session and that the new
prompt is used (saturation should collapse when asking for grayscale).
"""
import base64
import time

import cv2
import numpy as np

import fal_ext
from fal_ext import FalExt

fal_ext.DEBUG = True


class MockCOMP:
    path = "/mock"


def to_uri(img):
    ok, jpg = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 90])
    return "data:image/jpeg;base64," + base64.b64encode(jpg.tobytes()).decode()


PROMPT_A = "Substitute the current top with the outfit from the reference image"
PROMPT_B = "Convert the entire image to black and white grayscale, remove all color"


def saturation(frame):
    return float(cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)[:, :, 1].mean())


def wait_for_frame(ext, prompt, uri, timeout):
    t0 = time.time()
    while time.time() - t0 < timeout:
        ext.send_frame({"image_url": uri, "reference_image_url": uri, "prompt": prompt})
        data = ext.get_latest_frame()
        if data:
            return cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        time.sleep(0.5)
    return None


if __name__ == "__main__":
    person = cv2.imread("739784TOVA91055_F.jpg")
    uri = to_uri(person)

    ext = FalExt(MockCOMP())
    ext.start()

    frame = wait_for_frame(ext, PROMPT_A, uri, 150)
    print(f"phase A live: {frame is not None}")
    if frame is None:
        ext.stop()
        raise SystemExit("no frames in phase A")

    sat_a = []
    t0 = time.time()
    while time.time() - t0 < 20:
        f = wait_for_frame(ext, PROMPT_A, uri, 5)
        if f is not None:
            sat_a.append(saturation(f))

    print("--- changing prompt (should restart the session) ---")
    ext.latest_image_bytes = None
    frame = wait_for_frame(ext, PROMPT_B, uri, 150)
    print(f"phase B live: {frame is not None}")

    sat_b = []
    if frame is not None:
        time.sleep(8)  # let the new generation settle
        t0 = time.time()
        while time.time() - t0 < 20:
            f = wait_for_frame(ext, PROMPT_B, uri, 5)
            if f is not None:
                sat_b.append(saturation(f))

    ext.stop()
    time.sleep(2)

    if sat_a and sat_b:
        a, b = float(np.mean(sat_a)), float(np.mean(sat_b))
        print(f"\nsaturation with prompt A: {a:.1f}")
        print(f"saturation with prompt B: {b:.1f}")
        print("NEW PROMPT APPLIED" if b < a * 0.6 else "NEW PROMPT NOT APPLIED")
    else:
        print("not enough frames:", len(sat_a), len(sat_b))
