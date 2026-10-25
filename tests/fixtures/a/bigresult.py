def call(method, arg):
    if method == "obj":
        return object()
    if method == "nan":
        return float("nan")
    return "x" * int(arg)
