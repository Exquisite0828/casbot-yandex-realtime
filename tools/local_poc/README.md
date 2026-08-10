# Local Yandex Realtime Voice PoC

This is the Phase 2-only microphone → Yandex Realtime → speaker proof of
concept. It uses the current 2026 WebSocket schema, keeps one session open for
multiple turns, and flushes/cancels/truncates the old response on barge-in.

It does not contain ROS2 code and does not save microphone or response audio.

## Requirements

- Python 3.10 or newer.
- A working microphone and speaker; headphones are strongly recommended for
  the barge-in check.
- `YANDEX_API_KEY`, `YANDEX_FOLDER_ID`, `YANDEX_REALTIME_ENDPOINT`, and
  `YANDEX_MODEL_OR_AGENT` exported by the parent shell.

The API key is read only from `YANDEX_API_KEY`. Do not put it on the command
line, in this directory, or in a `.env` file.

Install the two local dependencies in an ignored virtual environment:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r tools/local_poc/requirements.txt
```

## Run

List PortAudio devices first:

```bash
.venv/bin/python tools/local_poc/realtime_voice_poc.py --list-devices
```

Start the live session with the model configured in
`YANDEX_MODEL_OR_AGENT`:

```bash
.venv/bin/python tools/local_poc/realtime_voice_poc.py
```

The default PCM format is signed 16-bit mono at 24 kHz, split into 20 ms raw
chunks. The session explicitly requests Russian input and the `dasha` voice.
Use `--input-device INDEX` and `--output-device INDEX` if the defaults are not
the desired devices.

For a bounded smoke run:

```bash
.venv/bin/python tools/local_poc/realtime_voice_poc.py --duration-seconds 60
```

The script logs event types, user transcripts, assistant text, first-audio
latency observations, and a final runtime summary. It never logs the API key or
raw audio.

## Human acceptance

In one uninterrupted process:

1. Ask a simple question in Russian and hear a Russian answer.
2. Continue for at least three natural turns and verify that context is kept.
3. While the assistant is audibly speaking, speak again and verify that the
   old audio stops and a new answer follows.
4. Keep the conversation running for a few minutes before pressing `Ctrl+C`.

Expected interruption events are local playback flush, `response.cancel` when
generation is still active, and `conversation.item.truncate` with the locally
played duration. A completed response may only need the truncate event.

## Explicit fallback

Do not fallback for network, credential, or local audio-device failures. Only
after a clear 260528 model/protocol incompatibility, run the documented Route A
fallback explicitly:

```bash
.venv/bin/python tools/local_poc/realtime_voice_poc.py \
  --model speech-realtime-250923
```

If neither model returns realtime audio, stop at Gate 2. Do not switch to an
STT → LLM → TTS architecture.
