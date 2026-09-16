"""Open a terminal window/tab running a command. Best effort per OS."""

import os
import shlex
import shutil
import subprocess
import sys

import sessions_common as common


def shell_line(argv):
    if sys.platform == "win32":
        return subprocess.list2cmdline(argv)
    return " ".join(shlex.quote(a) for a in argv)


def spawn_terminal(cwd: str, argv: list):
    env = common.child_env()
    try:
        if sys.platform == "win32":
            return _windows(cwd, argv, env)
        if sys.platform == "darwin":
            return _macos(cwd, argv, env)
        return _linux(cwd, argv, env)
    except OSError as exc:
        return False, str(exc)


def _windows(cwd, argv, env):
    flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    wt = shutil.which("wt.exe") or shutil.which("wt")
    if wt:
        # -w 0: most recent Windows Terminal window. cmd /k keeps the tab open on early exit.
        subprocess.Popen([wt, "-w", "0", "new-tab", "-d", cwd, "--title", "claude", "cmd", "/k"] + argv,
                         env=env, creationflags=flags, close_fds=True)
        return True, "Windows Terminal"
    command = 'start "claude" /D "%s" cmd /k %s' % (cwd, subprocess.list2cmdline(argv))
    subprocess.Popen(command, shell=True, env=env, creationflags=flags, close_fds=True)
    return True, "cmd"


def _macos(cwd, argv, env):
    script = 'tell application "Terminal" to do script "cd %s && %s"' % (shlex.quote(cwd), shell_line(argv))
    subprocess.Popen(["osascript", "-e", script, "-e", 'tell application "Terminal" to activate'], env=env)
    return True, "Terminal.app"


def _linux(cwd, argv, env):
    shell_command = "cd %s && %s; exec $SHELL" % (shlex.quote(cwd), shell_line(argv))
    candidates = [
        (os.environ.get("TERMINAL"), lambda t: [t, "-e", "bash", "-c", shell_command]),
        ("x-terminal-emulator", lambda t: [t, "-e", "bash", "-c", shell_command]),
        ("gnome-terminal", lambda t: [t, "--working-directory=" + cwd, "--", "bash", "-c", shell_command]),
        ("konsole", lambda t: [t, "--workdir", cwd, "-e", "bash", "-c", shell_command]),
        ("xfce4-terminal", lambda t: [t, "--working-directory=" + cwd, "-e", "bash -c %s" % shlex.quote(shell_command)]),
        ("xterm", lambda t: [t, "-e", "bash", "-c", shell_command]),
    ]
    for name, build in candidates:
        binary = shutil.which(name) if name else None
        if binary:
            subprocess.Popen(build(binary), env=env, start_new_session=True, close_fds=True)
            return True, name
    return False, "no terminal emulator found; run the command yourself"
