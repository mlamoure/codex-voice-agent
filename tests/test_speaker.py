"""The jitter buffer that turns bursty network audio into a steady 20 ms stream."""
import numpy as np

from codex_voice_agent.audio import FRAME, JitterBuffer


def frame(n=0):
    return np.full(FRAME, n, dtype=np.int16)


def test_silence_until_prefilled():
    jb = JitterBuffer()
    for i in range(JitterBuffer.PREFILL - 1):
        jb.push(frame(i))
        assert jb.pop() is None  # still filling: play silence
    jb.push(frame(99))
    assert jb.playing is False
    first = jb.pop()
    assert first is not None and jb.playing


def test_frames_come_out_in_order():
    jb = JitterBuffer()
    for i in range(JitterBuffer.PREFILL):
        jb.push(frame(i))
    assert [int(jb.pop()[0]) for _ in range(JitterBuffer.PREFILL)] == list(range(JitterBuffer.PREFILL))


def test_bursty_arrivals_play_without_underrun():
    jb = JitterBuffer()
    played = 0
    # a burst of 5 frames every 100 ms; the player pops one frame per 20 ms tick
    for tick in range(500):
        if tick % 5 == 0:
            for _ in range(5):
                jb.push(frame())
        if jb.pop() is not None:
            played += 1
    assert jb.underruns == 0
    assert played > 480


def test_running_dry_counts_an_underrun_and_refills():
    jb = JitterBuffer()
    for _ in range(JitterBuffer.PREFILL):
        jb.push(frame())
    for _ in range(JitterBuffer.PREFILL):
        assert jb.pop() is not None
    assert jb.pop() is None and jb.underruns == 1 and not jb.playing
    jb.push(frame())
    assert jb.pop() is None  # refilling again, not playing a lone frame


def test_backlog_is_trimmed_so_latency_cannot_grow():
    jb = JitterBuffer()
    for i in range(JitterBuffer.MAX + 7):
        jb.push(frame(i))
    assert len(jb) == JitterBuffer.MAX and jb.trimmed == 7
    assert int(jb.pop()[0]) == 7  # the oldest frames were dropped
