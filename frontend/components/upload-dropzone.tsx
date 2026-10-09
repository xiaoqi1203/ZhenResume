'use client';

import * as React from 'react';
import { FileText, UploadCloud, X } from 'lucide-react';
import { cn } from '@/lib/utils';

export const ACCEPTED_EXTENSIONS = ['.pdf', '.docx', '.png', '.jpg', '.jpeg', '.txt'] as const;
export const ACCEPT_ATTRIBUTE = ACCEPTED_EXTENSIONS.join(',');
export const MAX_FILE_SIZE = 10 * 1024 * 1024; // 10MB

export function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
}

/** 返回中文错误文案；合法时返回 null。 */
export function validateResumeFile(file: File): string | null {
  const name = file.name.toLowerCase();
  const ok = ACCEPTED_EXTENSIONS.some((ext) => name.endsWith(ext));
  if (!ok) {
    return `不支持的文件类型，仅支持 ${ACCEPTED_EXTENSIONS.join(' / ')}`;
  }
  if (file.size > MAX_FILE_SIZE) {
    return `文件大小 ${formatFileSize(file.size)} 超过 10MB 上限`;
  }
  return null;
}

export interface UploadDropzoneProps {
  file: File | null;
  onFileChange: (file: File | null) => void;
  onError?: (message: string | null) => void;
  disabled?: boolean;
  className?: string;
  hint?: string;
}

export function UploadDropzone({
  file,
  onFileChange,
  onError,
  disabled = false,
  className,
  hint = '支持 PDF / DOCX / PNG / JPG / TXT，单份不超过 10MB',
}: UploadDropzoneProps) {
  const inputRef = React.useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = React.useState(false);
  const [localError, setLocalError] = React.useState<string | null>(null);

  const reportError = React.useCallback(
    (message: string | null) => {
      setLocalError(message);
      onError?.(message);
    },
    [onError],
  );

  const accept = React.useCallback(
    (candidate: File | undefined | null) => {
      if (!candidate) return;
      const message = validateResumeFile(candidate);
      if (message) {
        reportError(message);
        onFileChange(null);
        return;
      }
      reportError(null);
      onFileChange(candidate);
    },
    [onFileChange, reportError],
  );

  const handleDrop = (event: React.DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    setDragging(false);
    if (disabled) return;
    const dropped = event.dataTransfer?.files?.[0];
    accept(dropped);
  };

  const handleSelect = (event: React.ChangeEvent<HTMLInputElement>) => {
    accept(event.target.files?.[0]);
    // 允许重复选择同一文件
    event.target.value = '';
  };

  const clear = () => {
    reportError(null);
    onFileChange(null);
    if (inputRef.current) inputRef.current.value = '';
  };

  return (
    <div className={cn('w-full', className)}>
      <input
        ref={inputRef}
        type="file"
        accept={ACCEPT_ATTRIBUTE}
        className="hidden"
        onChange={handleSelect}
        disabled={disabled}
      />

      {file ? (
        <div className="flex items-center justify-between gap-3 rounded-xl border border-brand-200 bg-brand-50/60 px-4 py-3">
          <div className="flex min-w-0 items-center gap-3">
            <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-white text-brand-600 ring-1 ring-brand-200">
              <FileText className="h-4 w-4" aria-hidden />
            </span>
            <div className="min-w-0">
              <p className="truncate text-sm font-medium text-slate-900" title={file.name}>
                {file.name}
              </p>
              <p className="text-xs text-slate-500">{formatFileSize(file.size)}</p>
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <button
              type="button"
              onClick={() => inputRef.current?.click()}
              disabled={disabled}
              className="rounded-md px-2 py-1 text-xs font-medium text-brand-700 hover:bg-white disabled:opacity-50"
            >
              重新选择
            </button>
            <button
              type="button"
              onClick={clear}
              disabled={disabled}
              aria-label="移除文件"
              className="rounded-md p-1.5 text-slate-400 hover:bg-white hover:text-slate-700 disabled:opacity-50"
            >
              <X className="h-4 w-4" aria-hidden />
            </button>
          </div>
        </div>
      ) : (
        <div
          role="button"
          tabIndex={0}
          aria-disabled={disabled}
          onClick={() => !disabled && inputRef.current?.click()}
          onKeyDown={(event) => {
            if (disabled) return;
            if (event.key === 'Enter' || event.key === ' ') {
              event.preventDefault();
              inputRef.current?.click();
            }
          }}
          onDragOver={(event) => {
            event.preventDefault();
            if (!disabled) setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={handleDrop}
          className={cn(
            'flex cursor-pointer flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed px-6 py-10 text-center transition-colors',
            'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-500',
            dragging
              ? 'border-brand-400 bg-brand-50'
              : 'border-slate-300 bg-slate-50/60 hover:border-brand-300 hover:bg-slate-50',
            disabled && 'pointer-events-none opacity-60',
          )}
        >
          <span className="flex h-11 w-11 items-center justify-center rounded-full bg-white text-brand-600 shadow-sm ring-1 ring-slate-200">
            <UploadCloud className="h-5 w-5" aria-hidden />
          </span>
          <p className="text-sm font-medium text-slate-800">
            拖拽简历到此处，或<span className="text-brand-600"> 点击选择文件</span>
          </p>
          <p className="text-xs text-slate-500">{hint}</p>
        </div>
      )}

      {localError ? <p className="mt-2 text-xs text-red-600">{localError}</p> : null}
    </div>
  );
}

export default UploadDropzone;
