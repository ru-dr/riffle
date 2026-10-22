"""doctor.py: OS / distro detection and per-package-manager install commands."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import doctor  # noqa: E402


def _plat(system, rel=None, have=(), root=False, wsl=False):
    return doctor.detect_platform(system=system, os_release=rel or {}, which=lambda t: t if t in have else None,
                                  root=root, wsl=wsl)


def test_linux_families_from_os_release():
    cases = [({"ID": "ubuntu", "ID_LIKE": "debian"}, "debian", "apt-get"),
             ({"ID": "linuxmint", "ID_LIKE": "ubuntu debian"}, "debian", "apt-get"),
             ({"ID": "fedora"}, "rhel", "dnf"),
             ({"ID": "rocky", "ID_LIKE": "rhel centos fedora"}, "rhel", "dnf"),
             ({"ID": "arch"}, "arch", "pacman"),
             ({"ID": "opensuse-tumbleweed", "ID_LIKE": "opensuse suse"}, "suse", "zypper"),
             ({"ID": "alpine"}, "alpine", "apk")]
    for rel, family, pm in cases:
        p = _plat("Linux", rel, have=(pm,))
        assert (p.family, p.pm) == (family, pm), rel


def test_unknown_distro_falls_back_to_whatever_pm_exists():
    p = _plat("Linux", {"ID": "weirdos"}, have=("zypper",))
    assert (p.family, p.pm) == ("other", "zypper")


def test_macos_and_windows():
    assert _plat("Darwin", have=("brew",)).pm == "brew"
    assert _plat("Darwin").pm is None
    assert _plat("Windows", have=("scoop", "choco")).pm == "scoop"       # winget first, then scoop
    assert _plat("Windows", have=("winget", "scoop")).pm == "winget"


def test_install_plan_per_family():
    deb = _plat("Linux", {"ID": "ubuntu"}, have=("apt-get",), root=True)
    assert doctor.install_plan("git", deb) == [["apt-get", "install", "-y", "git"]]
    assert doctor.install_plan("venv", deb)[0][:3] == ["apt-get", "install", "-y"]
    fed = _plat("Linux", {"ID": "fedora"}, have=("dnf",), root=True)
    assert doctor.install_plan("git", fed) == [["dnf", "install", "-y", "git"]]
    assert doctor.install_plan("venv", fed) == []                         # only Debian splits it out
    arch = _plat("Linux", {"ID": "arch"}, have=("pacman",), root=True)
    assert doctor.install_plan("git", arch) == [["pacman", "-S", "--noconfirm", "git"]]
    mac = _plat("Darwin", have=("brew",))
    assert doctor.install_plan("git", mac) == [["brew", "install", "git"]]
    assert doctor.install_plan("oasdiff", mac) == [["brew", "install", "oasdiff"]]
    win = _plat("Windows", have=("winget",))
    assert doctor.install_plan("git", win)[0][:4] == ["winget", "install", "--id", "Git.Git"]


def test_macos_without_brew_uses_xcode_tools_for_git():
    assert doctor.install_plan("git", _plat("Darwin")) == [["xcode-select", "--install"]]


def test_unprivileged_without_sudo_gives_no_plan(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", lambda t: None)
    p = _plat("Linux", {"ID": "ubuntu"}, have=("apt-get",), root=False)
    assert doctor.install_plan("git", p) == []
    assert "git" in doctor.install_hint("git", p)


def test_every_optional_tool_has_a_hint():
    p = _plat("Linux", {"ID": "ubuntu"})
    for tool in doctor.OPTIONAL_TOOLS:
        assert doctor.install_hint(tool, p)


def test_report_runs_without_a_terminal(capsys):
    doctor._report([doctor.Result("git", doctor.OK, "2.45", group="System"),
                    doctor.Result("buf", doctor.WARN, "protobuf (.proto) · not installed",
                                  group="API contract diff (optional)")])
    out = capsys.readouterr().out
    assert "OK" in out and "WARN" in out and "\033" not in out


def test_npm_without_write_access_goes_to_user_prefix(monkeypatch):
    p = _plat("Linux", {"ID": "fedora"}, have=("dnf",))
    monkeypatch.setattr(doctor.shutil, "which", lambda t: "/usr/bin/npm" if t == "npm" else None)
    monkeypatch.setattr(doctor.deps, "cmd_output", lambda c: "/usr/local")
    monkeypatch.setattr(doctor.os, "access", lambda *a: False)
    cmd = doctor.install_plan("buf", p)[0]
    assert cmd[:4] == ["npm", "install", "-g", "--prefix"] and cmd[4].endswith(".local")
    monkeypatch.setattr(doctor.os, "access", lambda *a: True)
    assert doctor.install_plan("buf", p) == [["npm", "install", "-g", "@bufbuild/buf"]]


def test_missing_node_or_go_is_installed_first(monkeypatch):
    p = _plat("Linux", {"ID": "fedora"}, have=("dnf",), root=True)
    monkeypatch.setattr(doctor.shutil, "which", lambda t: None)
    plan = doctor.install_plan("graphql-inspector", p)
    assert plan[0] == ["dnf", "install", "-y", "nodejs", "npm"]
    assert plan[1][:3] == ["npm", "install", "-g"] and plan[1][-1] == "@graphql-inspector/cli"
    assert len(plan) == 2
    assert doctor.install_plan("oasdiff", p) == [["dnf", "install", "-y", "golang"],
                                                 ["go", "install", "github.com/oasdiff/oasdiff@latest"]]


def test_no_node_and_no_installer_gives_empty_plan(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", lambda t: None)
    assert doctor.install_plan("graphql-inspector", _plat("Linux", {"ID": "weirdos"})) == []


def test_checks_crash_becomes_a_warning(monkeypatch):
    def boom(plat=None):
        raise RuntimeError("x")
    monkeypatch.setattr(doctor, "run_checks", boom)
    r = doctor._safe_checks(_plat("Linux"))
    assert r[0].status == doctor.WARN and "RuntimeError" in r[0].detail


def test_countdown_is_skipped_without_a_terminal():
    assert doctor.countdown(5) is True        # pytest has no TTY: returns at once


def test_team_store_line_reads_dot_env_and_never_fails(monkeypatch):
    import doctor
    monkeypatch.delenv("RIFFLE_SYNC_URL", raising=False)
    monkeypatch.delenv("RIFFLE_SYNC_TOKEN", raising=False)
    monkeypatch.setattr(doctor, "_read_env_file", lambda: {})
    name, status, detail, fix = doctor._team_store_check()
    assert name == "Join token" and status == doctor.WARN and "join code" in detail and fix == "join"
    monkeypatch.setattr(doctor, "_read_env_file", lambda: {"RIFFLE_SYNC_URL": "http://127.0.0.1:1", "RIFFLE_SYNC_TOKEN": "rw_x"})
    name, status, detail, fix = doctor._team_store_check()
    assert status == doctor.WARN and "cannot reach the store" in detail          # a problem is a warning, never a failure


def _fake_whoami(table):
    return lambda url=None, token=None, timeout=15.0: table.get(token, {"ok": False, "why": "the store did not accept this token (wrong, revoked or expired)"})


def test_read_and_admin_tokens_are_checked_for_their_own_kind(monkeypatch):
    import doctor
    import sync
    monkeypatch.setattr(sync, "whoami", _fake_whoami({
        "rd_ok": {"ok": False, "kind": "readers", "name": "maria", "why": "this is a reader token, not a PC token"},
        "ad_ok": {"ok": False, "kind": "admins", "name": "lead", "why": "this is an admin token, not a PC token"}}))
    env = {"RIFFLE_SYNC_URL": "https://s.example"}
    monkeypatch.setattr(doctor, "_read_env_file", lambda: env)
    for k in ("RIFFLE_SYNC_URL", "RIFFLE_SYNC_TOKEN", "RIFFLE_READ_TOKEN", "RIFFLE_ADMIN_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    assert doctor._read_token_check()[1:] [0] == doctor.WARN and doctor._read_token_check()[3] == "read"       # none saved: offer it
    assert doctor._admin_token_check() is None                                                                  # volunteers have no admin line
    env["RIFFLE_READ_TOKEN"] = "rd_ok"
    name, status, detail, fix = doctor._read_token_check()
    assert (name, status, fix) == ("Read token", doctor.OK, None) and "maria" in detail and "https://s.example" in detail
    env["RIFFLE_READ_TOKEN"] = "ad_ok"                                                                          # the wrong kind is named, not accepted
    assert doctor._read_token_check()[1] == doctor.WARN and "admin token, not a read token" in doctor._read_token_check()[2]
    env["RIFFLE_ADMIN_TOKEN"] = "ad_ok"
    assert doctor._admin_token_check()[:2] == ("Admin token", doctor.OK)
    env["RIFFLE_ADMIN_TOKEN"] = "rd_ok"
    assert doctor._admin_token_check()[1] == doctor.WARN
    del env["RIFFLE_SYNC_URL"]
    assert "join first" in doctor._read_token_check()[2]


def test_the_join_fix_checks_a_code_and_saves_it(monkeypatch, tmp_path, capsys):
    import doctor
    import join
    import sync
    monkeypatch.setattr(doctor, "ENV_PATH", str(tmp_path / ".env"))
    for k in ("RIFFLE_SYNC_URL", "RIFFLE_SYNC_TOKEN"):
        monkeypatch.setenv(k, "")
    good = join.encode("https://s.example", "rw_good", "abc12345", 2, 3, ["--repos-file", "repos_test.txt"])
    monkeypatch.setattr(sync, "whoami", _fake_whoami({"rw_good": {"ok": True, "name": "pc2of3-abc12345", "split": "abc12345"}}))
    answers = iter(["riffle1.garbage", good])
    monkeypatch.setattr(doctor.getpass, "getpass", lambda prompt="": next(answers))
    doctor._fix_join(None)
    out = capsys.readouterr().out
    assert "joined as pc2of3-abc12345" in out and "--shard 2/3" in out and "damaged" in out
    env = (tmp_path / ".env").read_text()
    assert "RIFFLE_SYNC_URL=https://s.example" in env and "RIFFLE_SYNC_TOKEN=rw_good" in env
    # a code the store refuses is not saved
    (tmp_path / ".env").unlink()
    monkeypatch.setattr(sync, "whoami", _fake_whoami({}))
    monkeypatch.setattr(doctor.getpass, "getpass", lambda prompt="": good)
    doctor._fix_join(None)
    assert not (tmp_path / ".env").exists() and "Ask the lead for a new code" in capsys.readouterr().out


def test_the_read_fix_only_accepts_a_read_token(monkeypatch, tmp_path, capsys):
    import doctor
    import sync
    monkeypatch.setattr(doctor, "ENV_PATH", str(tmp_path / ".env"))
    monkeypatch.setattr(doctor, "_read_env_file", lambda: {"RIFFLE_SYNC_URL": "https://s.example"})
    monkeypatch.delenv("RIFFLE_SYNC_URL", raising=False)
    monkeypatch.setenv("RIFFLE_READ_TOKEN", "")
    monkeypatch.setattr(sync, "whoami", _fake_whoami({"rd_good": {"ok": False, "kind": "readers", "name": "maria", "why": "x"},
                                                      "rw_pc": {"ok": True, "kind": "pcs", "name": "pc1", "why": None}}))
    answers = iter(["rw_pc", "rd_good"])
    monkeypatch.setattr(doctor.getpass, "getpass", lambda prompt="": next(answers))
    doctor._fix_read(None)
    out = capsys.readouterr().out
    assert "pcs token, not a read token" in out.replace("pc token", "pcs token") or "not a read token" in out
    assert "RIFFLE_READ_TOKEN=rd_good" in (tmp_path / ".env").read_text()


def test_the_join_check_warns_when_a_token_has_no_saved_join_code(monkeypatch, tmp_path):
    """A token in .env without .join.json means the lead's settings are not applied on this PC: that is not 'joined'."""
    import doctor
    import sync
    monkeypatch.setattr(doctor, "ENV_PATH", str(tmp_path / ".env"))
    (tmp_path / ".env").write_text("RIFFLE_SYNC_URL=https://s.example\nRIFFLE_SYNC_TOKEN=rw_x\n")
    for k in ("RIFFLE_SYNC_URL", "RIFFLE_SYNC_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(sync, "whoami", lambda u, t, **k: {"ok": True, "name": "pc1of2-abc", "split": "abc"})
    name, status, detail, fix = doctor._team_store_check()
    assert status == doctor.WARN and "no saved join code" in detail and fix == "join"
    (tmp_path / ".join.json").write_text("{}")
    assert doctor._team_store_check()[1] == doctor.OK
