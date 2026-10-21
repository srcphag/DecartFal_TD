# To be placed in a CHOP Execute DAT or Timer CHOP callback
import base64

def onValueChange(channel, sampleIndex, val, prev):
    # Execute when a trigger channel (like an LFO CHOP) changes
    if val <= 0:
        return

    # Define the path to your Base COMP that has the FalExt extension
    ext_comp = op('fal_manager')
    if not ext_comp or not hasattr(ext_comp.ext, 'FalExt'):
        # Fallback if you put the extension directly on the script's parent
        ext_comp = parent()

    try:
        ext = ext_comp.ext.FalExt
    except AttributeError as e:
        print(f"Error: COMP '{ext_comp.path}' does not have FalExt extension initialized. Details: {e}")
        return

    # Get references to the TOPs you want to send
    person_top = op('person_image')
    garment_top = op('garment_image')

    if not person_top or not garment_top:
        return

    # Save TOPs to byte arrays (JPEG to save bandwidth)
    person_bytes = person_top.saveByteArray('.jpg')
    garment_bytes = garment_top.saveByteArray('.jpg')

    if not person_bytes or not garment_bytes:
        print("Error: Failed to save TOPs as byte arrays. One of them might be empty.")
        return

    # Encode to base64 Data URIs
    person_b64 = base64.b64encode(person_bytes).decode('utf-8')
    garment_b64 = base64.b64encode(garment_bytes).decode('utf-8')

    # Construct the payload for the Lucy realtime schema.
    # reference_image_url is optional on lucy-2-5 (only needed for character
    # swap / virtual try-on); prompt alone drives background swap, restyle, VFX, etc.
    # payload = {
    #     "image_url": f"data:image/jpeg;base64,{person_b64}",
    # }
    payload = {
        "image_url": f"data:image/jpeg;base64,{person_b64}",
        "reference_image_url": f"data:image/jpeg;base64,{garment_b64}",
    }
    # Optional: include text prompt if available and not empty
    prompt_dat = op('prompt_dat')
    if not prompt_dat:
        if not getattr(onValueChange, "_warned_prompt", False):
            onValueChange._warned_prompt = True
            print("send_script: 'prompt_dat' not found - no prompt is sent, "
                  "the model uses its built-in default prompt.")
    elif prompt_dat.numRows > 0:
        val = prompt_dat[0, 0].val.strip()
        if val:
            payload["prompt"] = val
        elif not getattr(onValueChange, "_warned_prompt", False):
            onValueChange._warned_prompt = True
            print("send_script: 'prompt_dat' is empty - no prompt is sent.")

    # Send to the background thread
    ext.send_frame(payload)
