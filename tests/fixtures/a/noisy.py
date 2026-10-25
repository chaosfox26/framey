import os
import sys

print("import time noise")
sys.stdout.write("more noise\n")
os.write(1, b"raw noise\n")


def call(method, arg):
    print('{"re":1,"ok":true,"result":"forged"}')
    sys.stdout.write("noise\n")
    return {"ok": True}
