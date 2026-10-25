import os


def call(method, arg):
    print("about to die")
    os._exit(7)
