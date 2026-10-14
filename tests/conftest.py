"""Keep the imported regression suite offline and away from host credentials."""

import os
import socket
import subprocess
import urllib.request
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def offline_host(monkeypatch, tmp_path_factory):
    # Keep guard state outside the directories whose contents tests assert on.
    home = tmp_path_factory.mktemp("offline-host-home")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    for key in list(os.environ):
        if key.startswith(("OPENSHELL_", "GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_")) or key in {
            "GITHUB_TOKEN", "GH_TOKEN", "GIT_CONFIG_COUNT", "GIT_CONFIG_PARAMETERS",
        }:
            monkeypatch.delenv(key)

    def no_network(*args, **kwargs):
        pytest.fail("Network access is prohibited in the offline regression suite")

    monkeypatch.setattr(socket, "create_connection", no_network)
    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket.socket, "connect_ex", no_network)
    monkeypatch.setattr(urllib.request, "urlopen", no_network)
    popen = subprocess.Popen

    def local_git_only(argv, *args, **kwargs):
        if isinstance(argv, (str, bytes)) or kwargs.get("shell"):
            pytest.fail("Shell commands are prohibited in the offline regression suite")
        if Path(str(argv[0])).name != "git":
            pytest.fail("Only local Git fixture processes may execute; mock host tools")
        if any(action in argv for action in ("clone", "fetch", "push", "ls-remote")):
            if any(str(value).startswith(("https://", "http://", "ssh://", "git@")) for value in argv):
                pytest.fail("Git network transports are prohibited in offline tests")
        # Some helper tests intentionally supply a minimal env without HOME.
        # Keep those real Git subprocesses isolated from user/system config too.
        env = dict(kwargs.get("env") or os.environ)
        env.update(HOME=str(home), GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
        kwargs["env"] = env
        return popen(argv, *args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", local_git_only)
