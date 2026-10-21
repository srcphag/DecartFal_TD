"""
Resolution diagnostic: feeds a 1024x1024 frame, then a 512x512 frame,
and logs the size of every frame received back from the model.
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


def to_uri(img, quality=85):
    ok, jpg = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    return "data:image/jpeg;base64," + base64.b64encode(jpg.tobytes()).decode()


if __name__ == "__main__":
    base = cv2.imread("739784TOVA91055_F.jpg")
    big = cv2.resize(base, (1024, 1024))
    small = cv2.resize(base, (512, 512))
    print("input A:", big.shape[:2], " input B:", small.shape[:2])

    ext = FalExt(MockCOMP())
    ext.start()

    start = time.time()
    phase = "A"
    last = None
    seen = set()
    while time.time() - start < 90:
        elapsed = time.time() - start
        if elapsed > 40 and phase == "A":
            phase = "B"
            print("--- switching input to 512x512 ---")
        img = big if phase == "A" else small
        ext.send_frame({
            "image_url": to_uri(img),
            "reference_image_url": to_uri(big),
            "prompt": "Substitute the current top with the outfit from the reference image",
        })

        data = ext.get_latest_frame()
        if data and data is not last:
            last = data
            decoded = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
            shape = decoded.shape[:2] if decoded is not None else None
            sent = ext._input_frame.shape[:2]
            print(f"[{elapsed:5.1f}s] sent={sent} received={shape}")
            if shape not in seen:
                seen.add(shape)
                cv2.imwrite(f"res_out_{shape[0]}x{shape[1]}.jpg", decoded)
        time.sleep(0.4)

    ext.stop()
    time.sleep(2)
    print("received resolutions:", seen)
