class ProcessingError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message

    def public(self):
        return {"code": self.code, "message": self.message}
