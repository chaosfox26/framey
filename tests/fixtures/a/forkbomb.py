import subprocess
import sys
import time


def call(method, arg):
    if method == "hang":
        while True:
            time.sleep(0.01)
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    return child.pid
