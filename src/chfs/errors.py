"""领域错误定义。

领域层使用稳定错误码表达失败原因，HTTP 与 GUI 适配器分别决定如何展示，
从而避免业务逻辑依赖某一种交互协议。
"""


class CHFSError(Exception):
    """所有可预期业务错误的基类。"""

    code = "internal_error"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class InvalidPathError(CHFSError):
    code = "invalid_path"


class ResourceNotFoundError(CHFSError):
    code = "not_found"


class ResourceConflictError(CHFSError):
    code = "conflict"


class PermissionDeniedError(CHFSError):
    code = "permission_denied"


class UploadTooLargeError(CHFSError):
    code = "upload_too_large"


class IntegrityMismatchError(CHFSError):
    """上传内容与客户端声明的 SHA-256 不一致。"""

    code = "integrity_mismatch"


class AuthenticationError(CHFSError):
    code = "authentication_failed"


class InvalidConfigurationError(CHFSError):
    code = "invalid_configuration"
