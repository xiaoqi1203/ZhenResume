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


class QuotaExceeded(AppError):
    status_code = 402
    code = "QUOTA_EXCEEDED"
    message = "免费额度已用完，请选择付费套餐继续诊断"


class LlmError(AppError):
    status_code = 502
    code = "LLM_ERROR"
    message = "模型调用失败"
