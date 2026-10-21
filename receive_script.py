# To be placed in a Script TOP
import numpy as np
import cv2

_warned_size = False

def onCook(scriptOp):
    global _warned_size
    # This runs every frame TouchDesigner renders (as long as the TOP's
    # output is being used - keep the viewer on or connect it downstream)
    ext_comp = op('fal_manager')
    if not ext_comp or not hasattr(ext_comp.ext, 'FalExt'):
        ext_comp = parent()

    try:
        ext = ext_comp.ext.FalExt
    except AttributeError:
        # Extension not initialized yet
        return

    # Grab the latest decoded frame from the background thread
    img_bytes = ext.get_latest_frame()
    if not img_bytes:
        return

    # Decode the JPEG byte array into a numpy image
    nparr = np.frombuffer(img_bytes, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if img is None:
        return

    # OpenCV loads as BGR, TouchDesigner expects RGB
    img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)

    # Flip image vertically (OpenCV origin is top-left, TD origin is bottom-left)
    img = cv2.flip(img, 0)

    # copyNumpyArray requires the array to match the TOP's resolution exactly.
    # The model outputs 1280x720; resize if this TOP is set to something else.
    h, w = img.shape[:2]
    #print(h,w)

    if w != scriptOp.width or h != scriptOp.height:
        if not _warned_size:
            _warned_size = True
            print(f"receive_script: received frame is {w}x{h} but this Script TOP is "
                  f"{scriptOp.width}x{scriptOp.height} - resizing. Set the TOP's "
                  f"Output Resolution to {w}x{h} for a 1:1 image.")
        img = cv2.resize(img, (w, h))

    # Add an alpha channel (RGBA) for TouchDesigner's default pixel format
    rgba = cv2.cvtColor(img, cv2.COLOR_RGB2RGBA)

    # Copy the numpy array to the Script TOP's memory
    scriptOp.copyNumpyArray(rgba)
