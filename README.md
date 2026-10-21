# DecartFal_TD

TouchDesigner integration for the [fal.ai](https://fal.ai) **Decart Lucy** realtime
video-to-video API, using WebRTC for media transport and a fal WebSocket for signaling.

TouchDesigner feeds a live video stream into the model over a WebRTC video track; the
processed frames come back on the remote track and are handed to a Script TOP as JPEG
bytes. Prompt and reference-image (virtual try-on) updates are sent as control messages.

## Layout

| File | Purpose |
| --- | --- |
| `fal_ext.py` | The `FalExt` TouchDesigner extension: signaling, WebRTC negotiation, reconnect/backoff, frame plumbing. |
| `send_script.py` | CHOP/Timer Execute DAT callback — grabs TOPs, encodes them, and calls `FalExt.send_frame()`. |
| `receive_script.py` | Script TOP `onCook` — decodes the latest processed frame into the TOP. |
| `connect_toggle_script.py` | Parameter Execute DAT — start/stop the connection from a Toggle parameter. |
| `test_*.py` | Standalone (non-TouchDesigner) test scripts for the WebRTC pipeline, prompt changes, reconnection, and resolution. |
| `Fal_RT_API.toe` | The TouchDesigner project. |
| `Backup/` | Older snapshots of the `.toe` project. |
| `739784TOVA91055_F.jpg` | Test fixture image used by the `test_*.py` scripts. |

## Setup

1. **Install dependencies** into the TouchDesigner Python environment:

   ```
   pip install -r requirements.txt
   ```

   (In TouchDesigner this is typically done through the Python Env Manager / the
   project's `Decart_RT_API_vEnv` virtual environment.)

2. **Configure credentials.** Copy `.env.example` to `.env` and add your fal.ai key:

   ```
   FAL_KEY=your_key_id:your_key_secret
   ```

   `.env` is gitignored — never commit it.

3. Open `Fal_RT_API.toe` in TouchDesigner.

## TouchDesigner wiring

- Put `fal_ext.py` on a Base COMP and register the `FalExt` extension on it
  (the scripts expect the COMP to be named `fal_manager`).
- Add a `Connect` toggle parameter and attach `connect_toggle_script.py` to a
  Parameter Execute DAT to start/stop the session.
- Attach `send_script.py` to a CHOP/Timer Execute DAT to feed frames, and
  `receive_script.py` to a Script TOP to display the result.

## Configuration

Key constants live at the top of `fal_ext.py`:

| Constant | Default | Meaning |
| --- | --- | --- |
| `APP_ID` | `decart/lucy-2-5` | fal.ai model endpoint (use `decart/lucy2-vton` for try-on only). |
| `SEND_PERSON_IN_INPUT` | `False` | Send the person frame as a model input (required by `lucy2-vton`). |
| `PROMPT_EXPANSION` | `True` | Let Decart expand/enhance the prompt. |
| `JPEG_QUALITY` | `85` | Quality of frames handed to TouchDesigner. |
| `AUTO_START` | `False` | Connect on extension init instead of waiting for `start()`. |
| `DEBUG` | `False` | Log every signaling message. |

## Running the tests

The tests run outside TouchDesigner and require a valid `FAL_KEY`:

```
python test_webrtc.py       # end-to-end pipeline; writes webrtc_out_XXX.jpg
python test_prompt.py       # verifies a prompt change restarts the session
python test_reconnect.py    # reconnect behavior
python test_resolution.py   # resolution handling
```

## License

No license has been specified for this project yet.
