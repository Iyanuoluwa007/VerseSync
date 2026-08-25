# VerseSync + OBS Studio

Everything you need to get verses onto your stream. Start with the
Browser Source; the rest is optional.

- [The five-minute version](#the-five-minute-version)
- [Browser Source (the main integration)](#browser-source-the-main-integration)
- [Overlay options](#overlay-options)
- [Running OBS on a different machine](#running-obs-on-a-different-machine)
- [OBS WebSocket control (optional)](#obs-websocket-control-optional)
- [Virtual Camera](#virtual-camera)
- [Streaming to YouTube or Facebook (RTMP)](#streaming-to-youtube-or-facebook-rtmp)
- [Window and Display Capture (fallback)](#window-and-display-capture-fallback)
- [Latency and audio/video sync](#latency-and-audiovideo-sync)
- [Resolution and aspect ratio](#resolution-and-aspect-ratio)
- [Rehearsing without a microphone](#rehearsing-without-a-microphone)
- [Troubleshooting](#troubleshooting)

---

## The five-minute version

1. Start VerseSync: `uvicorn app.main:app --port 8000` from `backend/`.
2. In OBS: **Sources -> + -> Browser**.
3. URL: `http://localhost:8000/projector`
4. Width **1920**, Height **1080**.
5. Test it without touching a microphone:

```bash
curl -X POST http://localhost:8000/projector/show -H "Content-Type: application/json" -d "{\"text\":\"John three sixteen\"}"
```

The verse should appear in your OBS preview. That is the whole
integration; everything below is refinement.

---

## Browser Source (the main integration)

VerseSync serves an overlay page designed for OBS. It has a transparent
background by default, so it composites straight over your camera.

**Add the source**

| Setting | Value | Why |
|---|---|---|
| URL | `http://localhost:8000/projector` | Ask the server for the exact URL with `GET /projector/obs-url` |
| Width | `1920` | Match your canvas (Settings -> Video -> Base Resolution) |
| Height | `1080` | Same |
| FPS | `30` | The overlay only animates on verse changes; 60 buys nothing |
| Custom CSS | *(leave empty)* | OBS pre-fills a rule that fights the page's own styling |
| Shutdown source when not visible | **On** | Saves CPU; VerseSync restores the current verse on reconnect |
| Refresh browser when scene becomes active | Off | Not needed, and it causes a visible flash |
| Control audio via OBS | Off | The page is silent |

> **Clear the Custom CSS box.** OBS pre-populates it with
> `body { background-color: rgba(0,0,0,0); margin: 0px auto; overflow: hidden; }`.
> The `margin: 0px auto` in particular can shift the overlay. The page
> already sets everything it needs.

**Get the URL from the server** so you never guess the host:

```bash
curl http://localhost:8000/projector/obs-url
```

```json
{
  "url": "http://localhost:8000/projector",
  "obs_browser_source_settings": {
    "width": 1920, "height": 1080, "fps": 30,
    "shutdown_source_when_not_visible": true
  }
}
```

**Why "shutdown when not visible" is safe here.** Normally that setting
means the source comes back blank. VerseSync retains the verse currently
on screen and replays it to any client that connects, so switching back
to your preaching scene restores the verse that was showing. You can
confirm what would be restored:

```bash
curl http://localhost:8000/projector/state
```

---

## Overlay options

Append query parameters to the URL.

| Parameter | Values | Default | Notes |
|---|---|---|---|
| `theme` | `lowerthird`, `caption`, `fullscreen` | `lowerthird` | See below |
| `bg` | `transparent`, `dark`, `light`, `green` | `transparent` | `green` is for chroma-key paths that cannot carry alpha |
| `hold` | seconds, or `0` | `12` | `0` leaves the verse up until it is replaced |
| `fontScale` | `0.3` to `4.0` | `1.0` | Multiplies all text |
| `showRef` | `true`, `false` | `true` | Hide if OBS draws the reference itself |
| `showTranslation` | `true`, `false` | `true` | The `KJV` / `YOR` tag |
| `maxVerses` | `1` to `50` | `8` | Caps how much of a long range is drawn |
| `debug` | `true`, `false` | `false` | Shows a connection badge. **Never leave this on for a live stream.** |

**Themes**

- **`lowerthird`** - a panel in the bottom-left. The default, and the one
  that sits most comfortably over a camera shot of a preacher.
- **`caption`** - a full-width centred strip along the bottom, like
  broadcast subtitles.
- **`fullscreen`** - large centred text with no panel, for a scene with
  no camera behind it or a dedicated projector output.

Examples:

```
http://localhost:8000/projector?theme=caption&hold=0
http://localhost:8000/projector?theme=fullscreen&bg=dark&fontScale=1.4
http://localhost:8000/projector?theme=lowerthird&showTranslation=false
```

Defaults for every option can also be set in `backend/.env`
(`PROJECTOR_THEME`, `PROJECTOR_HOLD_SECONDS`, `PROJECTOR_FONT_SCALE`), so
the URL stays short.

---

## Running OBS on a different machine

Common when the streaming PC is separate from the machine with the
microphone.

1. Start VerseSync bound to the network, not just loopback:

   ```
   VERSESYNC_HOST=0.0.0.0
   ```

   ```bash
   uvicorn app.main:app --host 0.0.0.0 --port 8000
   ```

2. Find the VerseSync machine's LAN IP (`ipconfig` on Windows,
   `ip addr` elsewhere).

3. Use that IP in the Browser Source:
   `http://192.168.1.50:8000/projector`

4. Check the OBS machine can reach it: open the URL in a browser there
   first with `?debug=1` and confirm the badge reads `connected`.

> **Read [SECURITY.md](../SECURITY.md) before doing this.** On
> `0.0.0.0`, anyone who can reach the port can drive VerseSync unless you
> have set an admin PIN. Set one first:
>
> ```bash
> curl -X POST http://localhost:8000/auth/setup-pin -H "Content-Type: application/json" -d "{\"pin\":\"choose-a-real-one\"}"
> ```
>
> Even then there is no TLS, so use it only on a network you control and
> never port-forward it. See [docs/AUTH.md](AUTH.md).

---

## OBS WebSocket control (optional)

Lets a detected verse drive OBS itself: showing a scene item, or filling
an OBS text source. **You do not need this for the overlay.** Use it when
your lower-third is built in OBS rather than in the browser page.

**In OBS:** Tools -> WebSocket Server Settings -> tick *Enable WebSocket
server*. Note the port and click *Show Connect Info* for the password.

**In `backend/.env`:**

```ini
OBS_WS_ENABLED=true
OBS_WS_PORT=4455
OBS_WS_PASSWORD=the-password-obs-showed-you

# Show/hide a source when a verse appears/clears. Both are needed.
OBS_SCENE_NAME=Live Service
OBS_SCENE_ITEM=Verse Overlay

# Optional: keep an OBS text source in sync, e.g. "John 3:16 (KJV)"
OBS_TEXT_SOURCE=Verse Reference
```

Restart VerseSync, then check:

```bash
curl http://localhost:8000/obs/status
```

```json
{"enabled": true, "connected": true, "obs_version": "30.1.2", "...": "..."}
```

If OBS was not running when VerseSync started, reconnect without a
restart:

```bash
curl -X POST http://localhost:8000/obs/connect
```

**What happens when OBS goes away.** Nothing that matters. The controller
gives up after three consecutive failures and logs once; the Browser
Source overlay is unaffected and keeps showing verses. Call
`/obs/connect` when OBS is back.

The password is read from the environment only. It is never accepted over
the API and never returned by `/obs/status`, which reports only whether
one is configured.

---

## Virtual Camera

For Zoom, Google Meet or Teams.

1. Build a scene containing your camera plus the VerseSync Browser
   Source.
2. In the OBS **Controls** dock, click **Start Virtual Camera**.
3. In the meeting app, choose **OBS Virtual Camera** as the camera.

The overlay is composited into the camera output, so remote participants
see the verses. Nothing extra to configure on the VerseSync side.

---

## Streaming to YouTube or Facebook (RTMP)

1. **Settings -> Stream**, pick your service and paste the stream key.
2. **Settings -> Output**, set a bitrate your upload can actually
   sustain.
3. Stream.

The overlay is composited before encoding, so verses are burned into the
stream and add no latency of their own. There is nothing VerseSync-
specific to configure.

---

## Window and Display Capture (fallback)

Only if Browser Source is unavailable to you.

1. Open `http://localhost:8000/projector?bg=dark` in any browser and
   full-screen it (F11).
2. In OBS, add a **Window Capture** (or **Display Capture**) of it.
3. Crop to taste with a **Crop/Pad** filter.

For a transparent-looking result on a capture path that cannot carry
alpha, use `?bg=green` and add a **Chroma Key** filter in OBS with the
key colour set to green.

Browser Source is better in every respect: real alpha, lower CPU, no
stray window chrome, and it survives the browser being closed. Use this
only as a fallback.

---

## Latency and audio/video sync

Where the delay comes from, on the local-Whisper path:

| Stage | Typical | Notes |
|---|---|---|
| Voice activity detection closes a segment | ~0.5 s | `min_silence_duration_ms`, waits for the preacher to pause |
| Whisper transcription | 0.3-3 s | Depends heavily on model size and GPU vs CPU |
| Reference parsing | under 1 ms | Measured; see the performance notes in the README |
| Verse lookup | ~1 ms | Local SQLite |
| WebSocket to overlay | a few ms | Same machine |

So a verse typically appears **one to four seconds** after the reference
is spoken. That is inherent to waiting for a complete utterance before
transcribing it, not something VerseSync adds on top.

**If that offset bothers you on a recorded stream,** delay the *video*
to match rather than trying to speed up the overlay:

- Add a **Render Delay** filter to your camera source, or
- Use **Settings -> Advanced -> Stream Delay** to delay everything.

Match the delay to your observed lag (start around 2000 ms and adjust).
Note that a stream delay also delays audio, which keeps lip sync intact;
a render delay on video alone will *break* lip sync, so prefer the stream
delay unless you are delaying only the camera relative to a separately
delayed audio path.

**The cloud STT path adds network round-trip** on top of the above.
Prefer local Whisper when latency matters and you have the GPU for it.

---

## Resolution and aspect ratio

The overlay sizes everything in viewport units, so one URL works at any
16:9 resolution. Set the Browser Source dimensions to match your canvas:

| Canvas | Browser Source W x H |
|---|---|
| 1920x1080 (1080p) | 1920 x 1080 |
| 1280x720 (720p) | 1280 x 720 |
| 2560x1440 (1440p) | 2560 x 1440 |
| 3840x2160 (4K) | 3840 x 2160 |

**Do not** set the source to 1920x1080 and then scale it in the canvas:
the text will be resampled and look soft. Match the source to the canvas
and let the page lay itself out.

For vertical or square canvases (9:16, 1:1), use `theme=fullscreen` and
reduce `fontScale`; `lowerthird` assumes a wide frame.

---

## Rehearsing without a microphone

You can build and check the entire OBS scene before anyone speaks.

Show a verse by reference:

```bash
curl -X POST http://localhost:8000/projector/show -H "Content-Type: application/json" -d "{\"book\":\"ROM\",\"chapter\":8,\"verse_start\":28,\"verse_end\":30}"
```

Show one the way it would be spoken, through the real parser:

```bash
curl -X POST http://localhost:8000/projector/show -H "Content-Type: application/json" -d "{\"text\":\"turn with me to Romans eight twenty-eight\"}"
```

In Yoruba:

```bash
curl -X POST http://localhost:8000/projector/show -H "Content-Type: application/json" -d "{\"text\":\"Johanu kini ori keta ese kerin\",\"translation\":\"YOR\"}"
```

Clear it:

```bash
curl -X POST http://localhost:8000/projector/clear
```

This is also how an operator can drive the overlay manually from a phone
or a Stream Deck if the preacher goes off-script.

---

## Troubleshooting

**The Browser Source is blank.**
Open the same URL in a normal browser with `?debug=1`. A badge appears in
the top-right:

- `connected` - the link is fine; nothing has been sent yet. Push a test
  verse with `/projector/show`.
- `reconnecting...` - VerseSync is not reachable. Check the server is
  running and, if OBS is on another machine, that you used the LAN IP and
  started with `--host 0.0.0.0`.
- Nothing at all - the page did not load. Check the URL and the port.

**The overlay has a black box behind it.**
Either `bg` is not `transparent`, or OBS's pre-filled Custom CSS is
interfering. Clear the Custom CSS box.

**Verses appear, then vanish after a few seconds.**
That is `hold`, which defaults to 12 seconds. Use `?hold=0` to leave a
verse up until the next one replaces it.

**Text is too small or too large.**
Set the Browser Source dimensions to match your canvas rather than
scaling the source, then fine-tune with `?fontScale=`.

**The overlay stays blank after switching scenes.**
It should not: VerseSync replays the current verse on reconnect. Check
`GET /projector/state` to see what the server thinks is showing. If
`retained` is `null`, nothing was showing to restore.

**The overlay went stale after upgrading VerseSync.**
Right-click the Browser Source and choose **Refresh**. The page itself is
served with `no-store`, but OBS caches aggressively across restarts.

**The Browser Source returns 401 after I enabled authentication.**
Only if you also set `VERSESYNC_PUBLIC_PROJECTOR=false`. The display
surfaces stay open by default precisely because a Browser Source cannot
send an `Authorization` header. If you did lock them down, approve a
device with the `projector` role and append its token to the URL:

```
http://localhost:8000/projector?token=<device token>
```

See [docs/AUTH.md](AUTH.md#locking-down-the-projector-too).

**`/obs/status` says `connected: false`.**
Check *Enable WebSocket server* is ticked in OBS, that `OBS_WS_PORT`
matches the port shown there, and that `OBS_WS_PASSWORD` matches *Show
Connect Info*. Then `POST /obs/connect`. The error detail from that
endpoint names the specific failure.

**Nothing is detected when the preacher speaks.**
That is the STT path, not OBS. Check `GET /stt/status`, and see the
troubleshooting section in the [README](../README.md#troubleshooting).
