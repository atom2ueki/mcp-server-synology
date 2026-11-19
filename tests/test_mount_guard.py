"""Exercise the shell backstop with a stub rm; never delete a real mount."""

import os
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

ZSH = shutil.which("zsh")
pytestmark = pytest.mark.skipif(not ZSH, reason="mount guard requires zsh on macOS")
GUARD = Path(__file__).parents[1] / "skills/synology-recovery/scripts/mount-guard-rm"


def run_guard(tmp_path, arguments, *, mount_failure=False, mount_line=None):
    mountpoint = tmp_path / "NAS mount"
    mountpoint.mkdir(exist_ok=True)
    mounts = tmp_path / "mount-stub"
    line = mount_line or ("server on " + str(mountpoint) + " (smbfs, nodev)")
    mounts.write_text("#!/bin/sh\n" + ("exit 1\n" if mount_failure else
        f"printf '%s\\n' {shlex.quote(line)}\n"))
    mounts.chmod(0o700)
    rm = tmp_path / "rm-stub"
    marker = tmp_path / "executed"
    rm.write_text(f"#!/bin/sh\nprintf '%s\\n' invoked > {shlex.quote(str(marker))}\n")
    rm.chmod(0o700)
    script = tmp_path / "guard"
    script.write_text(GUARD.read_text().replace("REAL_RM=/bin/rm", f"REAL_RM={shlex.quote(str(rm))}")
                      .replace("MOUNT=/sbin/mount", f"MOUNT={shlex.quote(str(mounts))}"))
    result = subprocess.run([ZSH, "-f", str(script), *arguments], capture_output=True, text=True)
    return result, marker.exists()


@pytest.mark.parametrize("case", ["mount", "parent", "alias", "home", "mixed", "after", "separator"])
def test_recursive_mount_deletion_is_refused_before_any_target_runs(tmp_path, case):
    mountpoint = tmp_path / "NAS mount"
    alias = tmp_path / "link"
    alias.symlink_to(mountpoint)
    args = {
        "mount": ["-rf", str(mountpoint)],
        "parent": ["-rf", str(tmp_path)],
        "alias": ["-rf", str(alias)],
        "home": ["-rf", os.path.expanduser("~")],
        "mixed": ["-rf", str(tmp_path / "local"), str(mountpoint)],
        "after": [str(mountpoint), "-rf"],
        "separator": ["-rf", "--", str(mountpoint)],
    }[case]
    result, executed = run_guard(tmp_path, args)
    assert result.returncode == 1
    assert not executed
    assert "refusing" in result.stderr


def test_mountpoint_containing_on_fails_closed(tmp_path):
    # "server on /tmp/x/Work on NAS (smbfs)" is ambiguous; the guard must not
    # guess and must not exec rm.
    result, executed = run_guard(
        tmp_path, ["-rf", str(tmp_path / "local")],
        mount_line="//u@h/s on /tmp/x/Work on NAS (smbfs, nodev)",
    )
    assert result.returncode == 1
    assert not executed
    assert "ambiguous" in result.stderr


def test_unavailable_mount_table_fails_closed(tmp_path):
    result, executed = run_guard(tmp_path, ["-rf", str(tmp_path / "local")], mount_failure=True)
    assert result.returncode == 1
    assert not executed


def test_ordinary_local_directory_is_forwarded(tmp_path):
    result, executed = run_guard(tmp_path, ["-rf", str(tmp_path / "local")])
    assert result.returncode == 0
    assert executed


def test_symlink_then_parent_components_use_physical_resolution(tmp_path):
    mountpoint = tmp_path / "NAS mount"
    (mountpoint / "subdir").mkdir(parents=True)
    work = tmp_path / "work" / "deep"
    work.mkdir(parents=True)
    alias = work / "link"
    alias.symlink_to(mountpoint / "subdir")
    target = str(alias) + "/../../NAS mount"
    assert os.path.realpath(target) == str(mountpoint)
    result, executed = run_guard(tmp_path, ["-rf", target])
    assert result.returncode == 1
    assert not executed
