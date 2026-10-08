class Log:
    INFO = u"\u001b[36m"
    WARNING = u"\u001b[33m"
    ERROR = u"\u001b[31m"
    RESET = u"\u001b[0m"

    @classmethod
    def info(cls, message):
        print(f"{Log.INFO}[FNPORTING] {Log.RESET}{message}")
        _forward("", message)

    @classmethod
    def warn(cls, message):
        print(f"{Log.WARNING}[FNPORTING] {Log.RESET}{message}")
        _forward("Warning: ", message)

    @classmethod
    def error(cls, message):
        print(f"{Log.ERROR}[FNPORTING] {Log.RESET}{message}")
        _forward("Error: ", message)


def _forward(prefix, message):
    """MP: the line to the app's status log too."""
    try:
        from .material_porter import status
        status.post(prefix + str(message))
    except Exception:
        pass