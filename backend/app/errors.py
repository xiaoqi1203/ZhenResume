"""统一错误结构 —— 对应 docs/api-contract.md §8。

所有对外错误都必须长成 `{"detail": {"code": "...", "message": "..."}}`，
前端 lib/api.ts 依赖这个形状解析。
"""

from __future__ import annotations


class AppError(Exception):
    status_code: int = 500
    code: str = "INTERNAL_ERROR"
    message: str = "服务内部错误"

    def __init__(self, message: str | None = None, *, code: str | None = None, status_code: int | None = None):
        self.message = message or self.message
        if code:
            self.code = code
        if status_code:
            self.status_code = status_code
        super().__init__(self.message)

    def to_payload(self) -> dict:
        return {"detail": {"code": self.code, "message": self.message}}


class ValidationError(AppError):
    status_code = 400
    code = "VALIDATION_ERROR"
    message = "入参不合法"


class UnsupportedFileType(AppError):
    status_code = 415
    code = "UNSUPPORTED_FILE_TYPE"
    message = "不支持的文件类型，请上传 PDF / DOCX / PNG / JPG / TXT"


class FileTooLarge(AppError):
    status_code = 413
    code = "FILE_TOO_LARGE"
    message = "文件超过 10MB 上限"


class ResumeNotFound(AppError):
    status_code = 404
    code = "RESUME_NOT_FOUND"
    message = "简历不存在"


class JdNotFound(AppError):
    status_code = 404
    code = "JD_NOT_FOUND"
    message = "JD 不存在"


class TaskNotFound(AppError):
    status_code = 404
    code = "TASK_NOT_FOUND"
    message = "分析任务不存在"


class ReportNotFound(AppError):
    status_code = 404
    code = "REPORT_NOT_FOUND"
    message = "报告不存在"


class BatchNotFound(AppError):
    status_code = 404
    code = "BATCH_NOT_FOUND"
    message = "批次不存在"


class PlanNotFound(AppError):
    status_code = 404
    code = "PLAN_NOT_FOUND"
    message = "套餐不存在"


class TemplateNotFound(AppError):
    status_code = 404
    code = "TEMPLATE_NOT_FOUND"
    message = "简历模板不存在"


class PdfUnavailable(AppError):
    status_code = 503
    code = "PDF_UNAVAILABLE"
    message = (
        "服务端未配置 PDF 转换组件（LibreOffice），无法导出 PDF。"
        "请改用 DOCX 导出，或在编辑器里按 Ctrl+P 用浏览器打印为 PDF。"
    )


# --------------------------------------------------------------------------- #
# 账号与鉴权
# --------------------------------------------------------------------------- #
class Unauthorized(AppError):
    """401：没登录 / token 失效 / 验证码不对。前端拿到 code 后决定是否跳登录页。"""

    status_code = 401
    code = "UNAUTHORIZED"
    message = "请先登录"


class Forbidden(AppError):
    """403：已登录但没有权限（例如非管理员访问管理后台）。"""

    status_code = 403
    code = "FORBIDDEN"
    message = "没有权限执行该操作"


class Conflict(AppError):
    """409：唯一约束冲突（邮箱 / 手机号已被占用）。"""

    status_code = 409
    code = "CONFLICT"
    message = "该资源已存在"


class TooManyRequests(AppError):
    """429：发送验证码过于频繁 / 超出每日上限。"""

    status_code = 429
    code = "TOO_MANY_REQUESTS"
    message = "操作过于频繁，请稍后再试"

class QuotaExceeded(AppError):
    status_code = 402
    code = "QUOTA_EXCEEDED"
    message = "免费额度已用完，请选择付费套餐继续诊断"


class LlmError(AppError):
    status_code = 502
    code = "LLM_ERROR"
    message = "模型调用失败"
