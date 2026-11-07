"""Update notifications: version comparison, the GitHub reply parser, and the daemon's check."""
import asyncio
import json

from codex_voice_agent import __version__, daemon, update
from codex_voice_agent.config import Config, State

from conftest import make_daemon, start


def run(coro):
    return asyncio.run(coro)


def release_file(tmp_path, **fields):
    data = {"tag_name": "v2999.1.1", "html_url": f"{update.REPO_URL}/releases/tag/v2999.1.1",
            "draft": False, "prerelease": False, **fields}
    path = tmp_path / "latest.json"
    path.write_text(json.dumps(data))
    return path.as_uri()


def test_date_versions_compare_numerically():
    assert update.parse_version("v2026.11.6") == (2026, 11, 6)
    assert update.parse_version("2026.11.1.1") == (2026, 11, 1, 1)
    assert update.parse_version("nightly") is None
    assert update.is_newer("2026.11.10", "2026.11.9")
    assert update.is_newer("2026.11.1.1", "2026.11.1")
    assert not update.is_newer("2026.11.1", "2026.11.1.1")
    assert not update.is_newer("2026.11.6", "2026.11.6")
    assert not update.is_newer("garbage", "2026.11.6")


def test_latest_release_reads_github_reply(tmp_path):
    assert update.latest_release(release_file(tmp_path)) == {
        "version": "2999.1.1", "url": f"{update.REPO_URL}/releases/tag/v2999.1.1"}


def test_latest_release_ignores_prereleases_bad_tags_and_foreign_links(tmp_path):
    assert update.latest_release(release_file(tmp_path, prerelease=True)) is None
    assert update.latest_release(release_file(tmp_path, tag_name="latest")) is None
    got = update.latest_release(release_file(tmp_path, html_url="https://evil.example/x"))
    assert got["url"] == f"{update.REPO_URL}/releases"


def test_latest_release_survives_failures(tmp_path):
    assert update.latest_release((tmp_path / "missing.json").as_uri()) is None
    bad = tmp_path / "bad.json"
    bad.write_text("not json")
    assert update.latest_release(bad.as_uri()) is None


def test_config_and_state_keep_update_settings(tmp_path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text("check_updates = false\n")
    assert Config.load(cfg_file).check_updates is False
    assert Config.load(tmp_path / "none.toml").check_updates is True
    state_file = tmp_path / "state.json"
    State(notified_update="2999.1.1").save(state_file)
    assert State.load(state_file).notified_update == "2999.1.1"


def test_daemon_shows_newer_release_and_notifies_once(env, monkeypatch):
    tmp, _ = env
    monkeypatch.setattr(daemon, "UPDATE_FIRST_CHECK", 0)
    monkeypatch.setattr(update, "RELEASES_URL", release_file(tmp))

    async def body():
        d = make_daemon(tmp, [], check_updates=True)
        notified = []
        d.notify_update = notified.append
        d.state.notified_update = None
        task, c = await start(d, tmp)
        for _ in range(100):
            st = await c.cmd("state")
            if st.get("update"):
                break
            await asyncio.sleep(0.05)
        assert st["update"]["version"] == "2999.1.1" and st["version"] == __version__
        assert [n["version"] for n in notified] == ["2999.1.1"]
        assert d.state.notified_update == "2999.1.1"
        d.stop(); await task
    run(body())


def test_daemon_does_not_renotify_an_announced_version(env, monkeypatch):
    tmp, _ = env
    monkeypatch.setattr(daemon, "UPDATE_FIRST_CHECK", 0)
    monkeypatch.setattr(update, "RELEASES_URL", release_file(tmp))

    async def body():
        d = make_daemon(tmp, [], check_updates=True)
        notified = []
        d.notify_update = notified.append
        d.state.notified_update = "2999.1.1"
        task, c = await start(d, tmp)
        for _ in range(100):
            if (await c.cmd("state")).get("update"):
                break
            await asyncio.sleep(0.05)
        assert notified == []
        d.stop(); await task
    run(body())


def test_no_update_shown_for_current_release(env, monkeypatch):
    tmp, _ = env
    monkeypatch.setattr(daemon, "UPDATE_FIRST_CHECK", 0)
    monkeypatch.setattr(update, "RELEASES_URL", release_file(tmp, tag_name="v" + __version__))

    async def body():
        d = make_daemon(tmp, [], check_updates=True)
        d.notify_update = lambda latest: (_ for _ in ()).throw(AssertionError("notified"))
        task, c = await start(d, tmp)
        await asyncio.sleep(0.3)
        assert (await c.cmd("state"))["update"] is None
        d.stop(); await task
    run(body())
