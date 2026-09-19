"""Which parts of a clip are speech. Silero VAD, run straight from its ONNX file.

The energy threshold in features.py cannot tell speech from a noisy room: on our
noisy clips it called 99% of frames speech, so pause_ratio read ~0. Silero is a
2 MB neural VAD that runs on CPU in a few ms per second of audio.

Deliberately NOT the `silero-vad` pip package -- that drags in torch and
torchaudio (~700 MB) which is miserable on a Pi. onnxruntime has a Pi wheel and
the model file is vendored in models/ (MIT, see models/SILERO_LICENSE).

If onnxruntime is missing, speech_prob() returns None and features.py falls
back to the energy threshold -- slower to notice noise, but never broken.
"""
import os
import numpy as np

MODEL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models", "silero_vad.onnx")
SR = 16000
CHUNK = 512           # the 16 kHz model takes exactly 512 samples (32 ms)...
CONTEXT = 64          # ...plus the last 64 samples of the previous input

_session = None


def _load():
    global _session
    if _session is None:
        import onnxruntime as ort
        opts = ort.SessionOptions()
        opts.inter_op_num_threads = opts.intra_op_num_threads = 1
        _session = ort.InferenceSession(MODEL, sess_options=opts,
                                        providers=["CPUExecutionProvider"])
    return _session


def available():
    try:
        _load()
        return True
    except Exception:
        return False


class Stream:
    """Silero fed 512 samples at a time, as they arrive from the mic."""

    def __init__(self):
        self.sess = _load()
        self.state = np.zeros((2, 1, 128), dtype=np.float32)
        self.ctx = np.zeros((1, CONTEXT), dtype=np.float32)
        self.sr = np.array(SR, dtype=np.int64)

    def feed(self, chunk):
        """Speech probability for exactly CHUNK samples of float audio."""
        inp = np.concatenate([self.ctx, np.asarray(chunk, np.float32)[None, :]], axis=1)
        out, self.state = self.sess.run(None, {"input": inp, "state": self.state,
                                               "sr": self.sr})
        self.ctx = inp[:, -CONTEXT:]
        return float(out.item())


def speech_prob(x, sr):
    """Speech probability for each 32 ms chunk of x (float, -1..1). None if no VAD."""
    if sr != SR:
        return None                     # listen.py always records 16 kHz
    try:
        st = Stream()
    except Exception:
        return None
    x = np.asarray(x, dtype=np.float32)
    return np.array([st.feed(x[i * CHUNK:(i + 1) * CHUNK])
                     for i in range(len(x) // CHUNK)], dtype=np.float32)
