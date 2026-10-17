import type { Resume } from '@/lib/types';

/** 图片/扫描件解析失败时后端给的方法名，都是"没拿到文字"的信号。 */
const NO_TEXT_METHODS = new Set(['ocr-unavailable', 'ocr-failed', 'unsupported']);

/**
 * 上传后、建诊断任务前的闸门。
 *
 * 背景（真实存在的坑）：抽不到文字时后端**仍然会创建简历**，诊断流水线拿空文本
 * 也能"成功"跑完，产出一份分数极低的报告。错误只留在响应体的 `extract_note` 里，
 * 用户看到的是"分析完成"——这比直接报错更伤信任。
 *
 * 所以这里做**前置拦截**：没有正文就不让进诊断，并把后端的原因原样告诉用户，
 * 同时给出可执行的下一步（换格式 / 用编辑器粘贴）。
 *
 * @returns 需要展示给用户的拦截原因；`null` 表示可以继续。
 */
export function uploadGuardMessage(
  resume: Pick<Resume, 'raw_text' | 'extract_method' | 'extract_note'>,
): string | null {
  if (resume.raw_text && resume.raw_text.trim()) return null;

  const method = resume.extract_method ?? '';
  const detail = resume.extract_note?.trim();

  if (NO_TEXT_METHODS.has(method) || detail) {
    return [
      detail || '未能从这份文件里解析出任何文字。',
      '请改用 PDF / DOCX 重新上传，或点「用简历编辑器填写」直接粘贴内容后再诊断。',
    ].join(' ');
  }
  return '这份文件里没有可解析的文字（可能是空文件或扫描件）。请改用 PDF / DOCX，或直接用简历编辑器粘贴内容。';
}
