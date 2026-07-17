from fastapi import HTTPException, status


class InvalidTokenError(HTTPException):
    def __init__(
        self,
        detail: str = "Invalid token",
        status_code: int = status.HTTP_401_UNAUTHORIZED,
    ) -> None:
        super().__init__(status_code=status_code, detail=detail)


class InvalidInternalSecretError(HTTPException):
    def __init__(self, detail: str = "Invalid internal secret") -> None:
        super().__init__(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail)
