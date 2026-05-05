"""Microphone capture for the STT pipeline.

Uses sounddevice (PortAudio binding) which works cross-platform with
zero compilation hassle on Windows. The downstream pipeline expects
16 kHz mono float32 in 512-sample chunks (Silero VAD's required block
size), but we no longer ASK PortAudio to resample for us -- different
Windows host APIs handle that very differently:

- MME accepts arbitrary rates and resamples internally (sometimes well,
  sometimes silently mangled to zeros if channel mapping is off).
- WASAPI rejects non-native rates with PaErrorCode -9997.
- DirectSound varies wildly by driver.

So as of v0.4.3 we capture at the device's NATIVE sample rate and
channel count, then:
    1. Downmix to mono via channel mean.
    2. Linear-resample to 16 kHz in numpy (fine for speech / VAD / Whisper).
    3. Accumulate into a buffer; emit clean 512-sample mono float32
       chunks to the queue.

This sidesteps the device-18 sample-rate error entirely AND fixes
Windows mics that enumerate as 2 channels but only carry audio on one.

The capture runs in PortAudio's own thread; chunks land on a thread-safe
queue that the pipeline drains. Backpressure: if the consumer falls
behind by >100 chunks (~3.2s) we drop the oldest, which is safer than
blocking the audio thread.
"""
from __future__ import annotations

import logging
import queue
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16000              # what the pipeline downstream expects
CHANNELS = 1                     # mono after downmix
DTYPE = "float32"
BLOCK_SIZE = 512                 # 32 ms at 16 kHz; Silero VAD requirement

_MAX_QUEUED_CHUNKS = 100         # ~3.2s of audio


def _resample_linear(audio: np.ndarray, src_sr: int, dst_sr: int) -> np.ndarray:
    """Naive linear resample. Adequate for speech-band content fed into
    Whisper's 80-mel front-end. Don't use this for music."""
    if src_sr == dst_sr or audio.size == 0:
        return audio.astype(np.float32, copy=False)
    src_n = audio.shape[0]
    dst_n = int(round(src_n * dst_sr / src_sr))
    if dst_n <= 0:
        return np.zeros(0, dtype=np.float32)
    src_idx = np.linspace(0, src_n - 1, dst_n, dtype=np.float64)
    return np.interp(src_idx, np.arange(src_n), audio).astype(np.float32)


class MicrophoneStream:
    """Streams 16 kHz mono float32 chunks from a (possibly non-16k) mic."""

    def __init__(self, device: Optional[int | str] = None,
                 sample_rate: int = SAMPLE_RATE,
                 block_size: int = BLOCK_SIZE,
                 native_sample_rate: Optional[int] = None,
                 native_channels: Optional[int] = None):
        self.device = device
        self.sample_rate = sample_rate            # output rate (16k)
        self.block_size = block_size              # output block size (512)

        self._native_sr = native_sample_rate      # input rate (44100 typical)
        self._native_ch = native_channels         # input channels (1 or 2)
        self._native_block_frames: int = 0

        self._sd = None                            # set in start() (lazy)
        self._queue: queue.Queue[np.ndarray] = queue.Queue(maxsize=_MAX_QUEUED_CHUNKS)
        self._stream = None
        self._dropped = 0

        # Rolling buffer for the resampled mono signal so we can emit
        # exactly 512-sample chunks regardless of input block size.
        self._out_buf = np.zeros(0, dtype=np.float32)

        # Diagnostics
        self.last_chunk_rms: float = 0.0

    # -------- callback --------

    def _callback(self, indata, frames, time_info, status):
        if status:
            logger.warning("Audio stream status: %s", status)
        # indata: (frames, channels) float32
        if indata.ndim == 2 and indata.shape[1] > 1:
            mono = indata.mean(axis=1).astype(np.float32, copy=False)
        else:
            mono = np.asarray(indata, dtype=np.float32).reshape(-1)

        # Track RMS at the native rate -- we want to know if the mic
        # itself is producing audio, before we mess with anything.
        self.last_chunk_rms = float(np.sqrt(np.mean(mono * mono))) \
            if mono.size else 0.0

        # Resample to 16 kHz if needed.
        if self._native_sr and self._native_sr != self.sample_rate:
            mono = _resample_linear(mono, self._native_sr, self.sample_rate)

        # Append to rolling buffer; queue 512-sample chunks until depleted.
        self._out_buf = np.concatenate([self._out_buf, mono])
        while self._out_buf.shape[0] >= self.block_size:
            chunk = self._out_buf[:self.block_size].copy()
            self._out_buf = self._out_buf[self.block_size:]
            try:
                self._queue.put_nowait(chunk)
            except queue.Full:
                # Drop oldest, queue newest -- backpressure relief.
                try:
                    self._queue.get_nowait()
                except queue.Empty:
                    pass
                try:
                    self._queue.put_nowait(chunk)
                except queue.Full:
                    pass
                self._dropped += 1
                if self._dropped % 100 == 1:
                    logger.warning("Audio queue overflow; dropped %d chunks total",
                                   self._dropped)

    # -------- lifecycle --------

    def start(self) -> None:
        if self._stream is not None:
            return

        # Lazy import: sounddevice loads PortAudio at import time, which
        # we don't want to pay for if STT is never started.
        if self._sd is None:
            import sounddevice as sd
            self._sd = sd

        # Resolve native sample rate / channels for the chosen device.
        info = self._sd.query_devices(self.device, kind="input")
        if self._native_sr is None:
            self._native_sr = int(info["default_samplerate"])
        if self._native_ch is None:
            # Capture stereo when available; downmix to mono in callback.
            # Some Windows mics enumerate as 2-ch but only one channel
            # carries audio; the mean recovers it either way.
            self._native_ch = min(2, max(1, int(info["max_input_channels"])))

        # Native block size: roughly 32 ms at the input rate. Doesn't
        # have to match anything downstream because we re-buffer.
        self._native_block_frames = max(64, int(self._native_sr * 0.032))

        try:
            self._stream = self._sd.InputStream(
                samplerate=self._native_sr,
                channels=self._native_ch,
                dtype=DTYPE,
                blocksize=self._native_block_frames,
                device=self.device,
                callback=self._callback,
            )
            self._stream.start()
        except Exception as exc:
            logger.exception("Failed to open InputStream at %dHz/%dch on %s: %s",
                             self._native_sr, self._native_ch,
                             self.device or "default", exc)
            raise

        logger.info("Mic capture started "
                    "(device=%s, native_sr=%d, native_ch=%d -> "
                    "%dHz/%dch out, block=%d)",
                    self.device or "default",
                    self._native_sr, self._native_ch,
                    self.sample_rate, CHANNELS, self.block_size)

    def stop(self) -> None:
        if self._stream is None:
            return
        try:
            self._stream.stop()
            self._stream.close()
        finally:
            self._stream = None
        logger.info("Mic capture stopped")

    def get_chunk(self, timeout: float = 1.0) -> Optional[np.ndarray]:
        """Block until a 512-sample 16 kHz chunk is available."""
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

    @property
    def native_sample_rate(self) -> int:
        return self._native_sr or 0

    @property
    def native_channels(self) -> int:
        return self._native_ch or 0

    @staticmethod
    def list_devices() -> list[dict]:
        """Enumerate input-capable audio devices."""
        import sounddevice as sd
        out = []
        for i, dev in enumerate(sd.query_devices()):
            if dev.get("max_input_channels", 0) > 0:
                out.append({
                    "id": i,
                    "name": dev["name"],
                    "channels": dev["max_input_channels"],
                    "default_samplerate": dev["default_samplerate"],
                })
        return out
