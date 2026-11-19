"""API errors. Every error the API returns has a stable `error` code; docs/TROUBLESHOOTING.md lists them all."""


class ApiError(Exception):
    def __init__(self, status, code, message, **extra):
        super().__init__(message)
        self.status, self.code, self.message, self.extra = status, code, message, extra

    def body(self):
        return {'error': self.code, 'message': self.message, **self.extra}


def bad(msg, code='invalid', **extra):
    return ApiError(400, code, msg, **extra)


def denied(msg='You do not have permission for this action.', code='forbidden', **extra):
    return ApiError(403, code, msg, **extra)


def not_found(msg='Not found.', code='not_found'):
    return ApiError(404, code, msg)


def conflict(msg, code='conflict', **extra):
    return ApiError(409, code, msg, **extra)
