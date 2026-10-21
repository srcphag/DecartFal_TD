"""
Repro test for the restart issue: run several start/stop cycles and
check whether frames arrive on every cycle.
"""
import base64
import time

import fal_ext
from fal_ext import FalExt

fal_ext.DEBUG = True


class MockCOMP:
    path = "/mock"


if __name__ == "__main__":
    with open("739784TOVA91055_F.jpg", "rb") as f:
        uri = "data:image/jpeg;base64," + base64.b64encode(f.read()).decode()

    payload = {
        "image_url": uri,
        "reference_image_url": uri,
        "prompt": "Substitute the current top with the outfit from the reference image",
    }

    ext = FalExt(MockCOMP())

    for cycle in (1, 2, 3):
        print(f"\n===== CYCLE {cycle} =====")
        ext.start()
        t0 = time.time()
        got = False
        while time.time() - t0 < 45:
            ext.send_frame(payload)
            img = ext.get_latest_frame()
            if img:
                print(f"CYCLE {cycle}: frame received after {time.time() - t0:.1f}s")
                got = True
                break
            time.sleep(0.5)
        if not got:
            print(f"CYCLE {cycle}: NO FRAMES after 45s")
        ext.stop()
        time.sleep(3)

    print("\nDone.")
