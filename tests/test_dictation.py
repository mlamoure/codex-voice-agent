import asyncio
import json
import sys

import numpy as np
import pytest

from codex_voice_agent.config import Config, State
from codex_voice_agent.daemon import Daemon
from codex_voice_agent.dictation import superwhisper_active, watch_superwhisper
from codex_voice_agent.voice import MicTrack
from codex_voice_agent.audio import FRAME


def daemon():
    return Daemon(Config(osd=False, dictation_auto_pause=False), State())


def test_overlapping_pauses_and_manual_intent():
    async def run():
        d = daemon()
        await d.set_muted(False)
        a = await d.acquire_dictation('Tool A')
        b = await d.acquire_dictation('Tool A')
        assert d.muted and not d.user_muted
        await d.release_dictation(a)
        assert d.muted
        await d.set_muted('toggle')
        await d.release_dictation(b)
        assert d.muted and d.user_muted
        with pytest.raises(ValueError):
            await d.release_dictation(b)
        with pytest.raises(ValueError):
            await d.acquire_dictation('../bad\nsource')
        await d.set_muted(False)
        a = await d.acquire_dictation('Tool B')
        await d.set_muted(False)
        assert d.muted
        await d.release_dictation(a)
        assert not d.muted
    asyncio.run(run())


def test_already_muted_and_monitor_failure():
    async def run():
        d = daemon()
        await d._superwhisper_status(True, None)
        await d._superwhisper_status(False, None)
        assert d.muted
        await d.set_muted(False)
        await d._superwhisper_status(True, None)
        assert d.muted and not d.user_muted
        await d._superwhisper_status(None, 'stream closed')
        await d._superwhisper_status(False, None)
        assert d.muted and d.user_muted
        await d.set_muted(False)
        await d._superwhisper_status(True, None)
        d._mute_after_failure('voice lost')
        await d._superwhisper_status(False, None)
        assert d.muted
    asyncio.run(run())


def test_startup_and_other_adapter_do_not_clear_hook():
    async def run():
        d = daemon()
        await d._superwhisper_status(None, 'connecting')
        await d.set_muted(False)
        assert d.muted
        token = await d.acquire_dictation('Other')
        await d._superwhisper_status(False, None)
        assert d.muted
        await d.release_dictation(token)
        assert not d.muted
        with pytest.raises(ValueError):
            await d.release_dictation('adapter:superwhisper')
    asyncio.run(run())


def test_frames_silenced_and_queue_discarded_before_resume():
    async def run():
        d = daemon()
        q = asyncio.Queue()
        class Voice:
            mic_frames = q
            connected = True
        d.voice = Voice()
        await d.set_muted(False)
        track = MicTrack(q, lambda: d.muted)
        q.put_nowait(np.ones(FRAME, dtype=np.int16))
        token = await d.acquire_dictation('Test')
        assert q.empty()
        q.put_nowait(np.ones(FRAME, dtype=np.int16))
        assert not (await track.recv()).to_ndarray().any()
        q.put_nowait(np.ones(FRAME, dtype=np.int16))
        await d.release_dictation(token)
        assert q.empty()
        q.put_nowait(np.ones(FRAME, dtype=np.int16))
        assert (await track.recv()).to_ndarray().all()
    asyncio.run(run())


@pytest.mark.parametrize('state', ['recording', 'transcribing', 'processing', 'new-phase', 'error'])
def test_nonidle_states_pause(state):
    assert superwhisper_active(json.dumps({'alt': state}).encode())


def test_status_validation():
    assert not superwhisper_active(b'{"alt":"idle"}')
    for data in [b'bad', b'[]', b'{}', b'{"alt":null}']:
        with pytest.raises(ValueError):
            superwhisper_active(data)


def test_watcher_stream_failure_and_cleanup(tmp_path):
    script = tmp_path / 'superwhisper'
    script.write_text(f'#!{sys.executable}\nprint({repr(json.dumps({"alt": "recording"}))}, flush=True)\n')
    script.chmod(0o755)
    async def run():
        reports = []
        failed = asyncio.Event()
        async def report(active, error):
            reports.append((active, error))
            if error == 'status stream closed':
                failed.set()
        task = asyncio.create_task(watch_superwhisper(report, str(script)))
        await asyncio.wait_for(failed.wait(), 5)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        assert (True, None) in reports
        assert reports[-1] == (None, 'status stream closed')
    asyncio.run(run())


def test_config_switch(tmp_path):
    cfg = tmp_path / 'config.toml'
    cfg.write_text('dictation_auto_pause = false\n')
    assert not Config.load(cfg).dictation_auto_pause
    cfg.write_text('dictation_auto_pause = "false"\n')
    assert Config.load(cfg).problems


def test_control_socket_acknowledges_pause_before_return(tmp_path):
    async def run():
        d = daemon()
        d.socket = tmp_path / 'control.sock'
        await d._start_server()
        await d.set_muted(False)
        reader, writer = await asyncio.open_unix_connection(str(d.socket))
        async def request(value):
            writer.write((json.dumps(value) + '\n').encode())
            await writer.drain()
            return json.loads(await reader.readline())
        try:
            result = await request({'cmd': 'dictation_acquire', 'source': 'Test'})
            assert result['ok'] and result['muted'] and d.muted
            result = await request({'cmd': 'dictation_release', 'token': result['token']})
            assert result['ok'] and not d.muted
            assert not (await request({'cmd': 'dictation_acquire', 'source': []}))['ok']
        finally:
            writer.close()
            await writer.wait_closed()
            await d._shutdown()
            await d._server.wait_closed()
    asyncio.run(run())


def test_watcher_terminates_child_on_cancel(tmp_path):
    script = tmp_path / 'superwhisper'
    script.write_text(f'#!{sys.executable}\nimport time\nprint(\'{"{\"alt\":\"idle\"}"}\', flush=True)\ntime.sleep(60)\n')
    script.chmod(0o755)
    async def run():
        ready = asyncio.Event()
        async def report(active, error):
            if active is False:
                ready.set()
        task = asyncio.create_task(watch_superwhisper(report, str(script)))
        await asyncio.wait_for(ready.wait(), 5)
        task.cancel()
        result = await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 3)
        assert isinstance(result[0], asyncio.CancelledError)
    asyncio.run(run())
