import time


def call(method, arg):
    if method == "quick":
        return "quick"
    while True:
        time.sleep(0.01)
