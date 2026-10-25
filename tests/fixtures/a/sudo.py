import subprocess


def call(method, arg):
    try:
        done = subprocess.run(["sudo", "-n", "true"], capture_output=True, timeout=5)
        return {"code": done.returncode}
    except (OSError, subprocess.SubprocessError) as e:
        return {"error": str(e)}
