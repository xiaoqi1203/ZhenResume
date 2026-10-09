'use client';

import * as React from 'react';
import Link from 'next/link';
import {
  AlertTriangle,
  Download,
  Info,
  Loader2,
  Play,
  RefreshCw,
  Upload,
} from 'lucide-react';
import { ApiError, createBatch, createJd, downloadBatchExport, getBatch, uploadResume } from '@/lib/api';
import type { BatchItem, BatchStatusResponse } from '@/lib/types';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Progress } from '@/components/ui/progress';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { Textarea } from '@/components/ui/textarea';
import { RiskBadge } from '@/components/risk-badge';
import { EmptyState, ErrorBanner, InlineSpinner } from '@/components/states';
import { ACCEPT_ATTRIBUTE, formatFileSize, validateResumeFile } from '@/components/upload-dropzone';

const POLL_INTERVAL_MS = 2000;

type UploadState = 'waiting' | 'uploading' | 'done' | 'failed';

interface UploadEntry {
  id: string;
  file: File;
  state: UploadState;
  resumeId: string | null;
  message: string | null;
}

const UPLOAD_STATE_TEXT: Record<UploadState, string> = {
  waiting: '等待上传',
  uploading: '上传中…',
  done: '已上传',
  failed: '失败',
};

const ITEM_STATUS_TEXT: Record<string, string> = {
  pending: '排队中',
  running: '分析中',
  done: '已完成',
  failed: '失败',
};

function describeError(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error) return error.message;
  return '发生未知错误，请稍后重试';
}

function makeId(file: File, index: number): string {
  return `${index}-${file.name}-${file.size}-${file.lastModified}`;
}

export default function HrPage() {
  const [jdText, setJdText] = React.useState('');
  const [jdTitle, setJdTitle] = React.useState('');
  const [entries, setEntries] = React.useState<UploadEntry[]>([]);
  const [error, setError] = React.useState<string | null>(null);
  const [fileWarning, setFileWarning] = React.useState<string | null>(null);
  const [starting, setStarting] = React.useState(false);
  const [batch, setBatch] = React.useState<BatchStatusResponse | null>(null);
  const [batchError, setBatchError] = React.useState<string | null>(null);
  const [batchLoading, setBatchLoading] = React.useState(false);
  const [csvExporting, setCsvExporting] = React.useState(false);

  const fileInputRef = React.useRef<HTMLInputElement>(null);
  const jdIdRef = React.useRef<string | null>(null);

  const ensureJdId = React.useCallback(async (): Promise<string> => {
    if (jdIdRef.current) return jdIdRef.current;
    const jd = await createJd({
      title: jdTitle.trim() || '批量初筛岗位',
      raw_text: jdText.trim(),
    });
    jdIdRef.current = jd.jd_id;
    return jd.jd_id;
  }, [jdText, jdTitle]);

  const addFiles = (files: FileList | null) => {
    if (!files || files.length === 0) return;
    const accepted: UploadEntry[] = [];
    const rejected: string[] = [];

    Array.from(files).forEach((file) => {
      const problem = validateResumeFile(file);
      if (problem) {
        rejected.push(`${file.name}：${problem}`);
        return;
      }
      accepted.push({
        id: makeId(file, accepted.length),
        file,
        state: 'waiting',
        resumeId: null,
        message: null,
      });
    });

    setFileWarning(rejected.length > 0 ? `已跳过 ${rejected.length} 个文件 · ${rejected.join('；')}` : null);
    if (accepted.length > 0) setEntries((prev) => [...prev, ...accepted]);
  };

  const removeEntry = (id: string) => {
    setEntries((prev) => prev.filter((entry) => entry.id !== id));
  };

  /* -------------------- 轮询批次 -------------------- */
  React.useEffect(() => {
    const batchId = batch?.batch_id;
    if (!batchId) return;
    if (batch.status === 'done' || batch.status === 'failed') return;

    let cancelled = false;
    const timer = setTimeout(async () => {
      try {
        const next = await getBatch(batchId);
        if (!cancelled) {
          setBatch(next);
          setBatchError(null);
        }
      } catch (err) {
        if (!cancelled) setBatchError(describeError(err));
      }
    }, POLL_INTERVAL_MS);

    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [batch]);

  const doneCount = entries.filter((entry) => entry.state === 'done').length;
  const uploadProgress = entries.length === 0 ? 0 : Math.round((doneCount / entries.length) * 100);
  const uploading = entries.some((entry) => entry.state === 'uploading');
  const busy = starting || uploading;

  const startBatch = async () => {
    setError(null);
    setBatchError(null);
    setBatch(null);

    if (jdText.trim().length < 20) {
      setError('请先粘贴岗位 JD，至少 20 个字，用于生成统一的筛选基准。');
      return;
    }
    if (entries.length === 0) {
      setError('请先选择至少一份简历文件。');
      return;
    }

    setStarting(true);
    const resumeIds: string[] = [];
    const failures: string[] = [];

    for (const entry of entries) {
      if (entry.state === 'done' && entry.resumeId) {
        resumeIds.push(entry.resumeId);
        continue;
      }
      setEntries((prev) =>
        prev.map((item) =>
          item.id === entry.id ? { ...item, state: 'uploading', message: null } : item,
        ),
      );
      try {
        const resume = await uploadResume(entry.file, entry.file.name);
        resumeIds.push(resume.resume_id);
        setEntries((prev) =>
          prev.map((item) =>
            item.id === entry.id
              ? { ...item, state: 'done', resumeId: resume.resume_id, message: null }
              : item,
          ),
        );
      } catch (err) {
        const message = describeError(err);
        failures.push(`${entry.file.name}：${message}`);
        setEntries((prev) =>
          prev.map((item) =>
            item.id === entry.id ? { ...item, state: 'failed', message } : item,
          ),
        );
      }
    }

    if (resumeIds.length === 0) {
      setStarting(false);
      setError(failures.length > 0 ? `全部简历上传失败：${failures.join('；')}` : '没有可用的简历。');
      return;
    }

    try {
      const created = await createBatch({ jd_id: await ensureJdId(), resume_ids: resumeIds });
      setBatch({
        batch_id: created.batch_id,
        status: created.status,
        total: created.total,
        finished: 0,
        items: [],
      });
    } catch (err) {
      setError(describeError(err));
    } finally {
      setStarting(false);
    }
  };

  const refreshBatch = async () => {
    const batchId = batch?.batch_id;
    if (!batchId) return;
    setBatchLoading(true);
    try {
      setBatch(await getBatch(batchId));
      setBatchError(null);
    } catch (err) {
      setBatchError(describeError(err));
    } finally {
      setBatchLoading(false);
    }
  };

  const exportCsv = async () => {
    const batchId = batch?.batch_id;
    if (!batchId || csvExporting) return;
    setCsvExporting(true);
    setBatchError(null);
    try {
      // 走 fetch 而不是 <a href download>：批次导出按 X-Device-Id 校验归属，
      // 浏览器原生导航不带这个请求头，会直接 404 BATCH_NOT_FOUND。
      const { blob, contentDisposition } = await downloadBatchExport(batchId);
      const utf8 = /filename\*=UTF-8''([^;]+)/i.exec(contentDisposition ?? '');
      let filename = `zhenresume-batch-${batchId}.csv`;
      if (utf8?.[1]) {
        try {
          filename = decodeURIComponent(utf8[1].trim().replace(/^"|"$/g, ''));
        } catch {
          /* 解析失败就用兜底文件名 */
        }
      }
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = filename;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 4000);
    } catch (err) {
      setBatchError(describeError(err));
    } finally {
      setCsvExporting(false);
    }
  };

  const items: BatchItem[] = batch?.items ?? [];
  const total = batch?.total ?? entries.length;
  const finished = batch?.finished ?? 0;
  const batchPercent = total === 0 ? 0 : Math.round((finished / total) * 100);

  return (
    <div className="space-y-6">
      <header className="space-y-2">
        <h1 className="text-xl font-semibold tracking-tight text-slate-900 sm:text-2xl">
          HR 批量初筛
        </h1>
        <p className="max-w-3xl text-sm leading-relaxed text-slate-600">
          上传岗位 JD 与多份简历，系统按统一标准并行分析并按总分排序，帮助你先看值得细读的简历。
        </p>
      </header>

      <div className="flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" aria-hidden />
        <p>
          系统不替 HR 做录用决策：排序结果只用于提示阅读优先级，所有录用判断仍需人工结合岗位实际、面试与合规要求作出。
        </p>
      </div>

      {error ? <ErrorBanner message={error} onRetry={() => setError(null)} /> : null}
      {fileWarning ? (
        <div className="flex items-start gap-2 rounded-lg border border-slate-200 bg-slate-50 px-4 py-3 text-xs text-slate-600">
          <Info className="mt-0.5 h-3.5 w-3.5 shrink-0 text-slate-400" aria-hidden />
          <span>{fileWarning}</span>
        </div>
      ) : null}

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>1. 岗位 JD</CardTitle>
            <p className="text-sm text-slate-500">所有简历都会与这份 JD 做匹配评分。</p>
          </CardHeader>
          <CardContent className="space-y-3">
            <Input
              value={jdTitle}
              onChange={(event) => setJdTitle(event.target.value)}
              disabled={busy}
              placeholder="岗位名称（选填），如：后端开发工程师"
            />
            <Textarea
              value={jdText}
              onChange={(event) => setJdText(event.target.value)}
              disabled={busy}
              rows={10}
              placeholder={'岗位职责：……\n任职要求：……'}
            />
            <p className="text-right text-xs text-slate-400">{jdText.trim().length} 字</p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>2. 批量上传简历</CardTitle>
            <p className="text-sm text-slate-500">支持一次选择多份文件，逐个上传。</p>
          </CardHeader>
          <CardContent className="space-y-3">
            <input
              ref={fileInputRef}
              type="file"
              multiple
              accept={ACCEPT_ATTRIBUTE}
              className="hidden"
              onChange={(event) => {
                addFiles(event.target.files);
                event.target.value = '';
              }}
              disabled={busy}
            />
            <button
              type="button"
              onClick={() => !busy && fileInputRef.current?.click()}
              disabled={busy}
              className="flex w-full flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed border-slate-300 bg-slate-50/60 px-6 py-8 text-center transition-colors hover:border-brand-300 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-60"
            >
              <span className="flex h-10 w-10 items-center justify-center rounded-full bg-white text-brand-600 shadow-sm ring-1 ring-slate-200">
                <Upload className="h-4 w-4" aria-hidden />
              </span>
              <span className="text-sm font-medium text-slate-800">点击选择多份简历</span>
              <span className="text-xs text-slate-500">
                支持 PDF / DOCX / PNG / JPG / TXT，单份不超过 10MB
              </span>
            </button>

            {entries.length > 0 ? (
              <div className="space-y-2">
                <div className="flex items-center justify-between text-xs text-slate-500">
                  <span>
                    共 {entries.length} 份 · 已上传 {doneCount} 份
                  </span>
                  <button
                    type="button"
                    onClick={() => setEntries([])}
                    disabled={busy}
                    className="text-slate-500 hover:text-slate-800 disabled:opacity-50"
                  >
                    清空
                  </button>
                </div>
                <Progress value={uploadProgress} />
                <ul className="max-h-64 space-y-1.5 overflow-y-auto pr-1">
                  {entries.map((entry) => (
                    <li
                      key={entry.id}
                      className="flex items-center justify-between gap-3 rounded-lg border border-slate-200 px-3 py-2"
                    >
                      <div className="min-w-0">
                        <p className="truncate text-sm text-slate-800" title={entry.file.name}>
                          {entry.file.name}
                        </p>
                        <p className="text-xs text-slate-500">
                          {formatFileSize(entry.file.size)} · {UPLOAD_STATE_TEXT[entry.state]}
                          {entry.message ? ` · ${entry.message}` : ''}
                        </p>
                      </div>
                      <button
                        type="button"
                        onClick={() => removeEntry(entry.id)}
                        disabled={busy}
                        className="shrink-0 text-xs text-slate-400 hover:text-red-600 disabled:opacity-50"
                      >
                        移除
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            ) : (
              <p className="text-xs text-slate-400">尚未选择文件。</p>
            )}
          </CardContent>
        </Card>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <Button onClick={startBatch} disabled={busy} size="lg">
          {busy ? (
            <>
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
              {uploading ? '正在上传简历…' : '正在创建批次…'}
            </>
          ) : (
            <>
              <Play className="h-4 w-4" aria-hidden />
              启动初筛
            </>
          )}
        </Button>
        {busy ? <InlineSpinner label={`已完成 ${doneCount} / ${entries.length} 份上传`} /> : null}
      </div>

      {/* 批次结果 */}
      {batch ? (
        <Card>
          <CardHeader>
            <CardTitle className="flex flex-wrap items-center justify-between gap-2">
              <span>初筛结果</span>
              <span className="flex items-center gap-2">
                <Button variant="outline" size="sm" onClick={refreshBatch} disabled={batchLoading}>
                  {batchLoading ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
                  ) : (
                    <RefreshCw className="h-3.5 w-3.5" aria-hidden />
                  )}
                  刷新
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => void exportCsv()}
                  disabled={csvExporting}
                >
                  {csvExporting ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
                  ) : (
                    <Download className="h-3.5 w-3.5" aria-hidden />
                  )}
                  导出 CSV
                </Button>
              </span>
            </CardTitle>
            <p className="text-sm text-slate-500">
              批次 {batch.batch_id} · 状态 {ITEM_STATUS_TEXT[batch.status] ?? batch.status} · 已完成{' '}
              {finished} / {total}
            </p>
          </CardHeader>
          <CardContent className="space-y-3">
            <Progress value={batchPercent} />

            {batchError ? (
              <ErrorBanner message={batchError} onRetry={() => void refreshBatch()} />
            ) : null}

            {items.length === 0 ? (
              <EmptyState
                title="暂未返回结果"
                description="批次已创建，系统正在逐份分析简历，结果会按总分从高到低自动出现在这里。"
              />
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-14">排名</TableHead>
                    <TableHead>简历</TableHead>
                    <TableHead className="w-20">总分</TableHead>
                    <TableHead className="w-20">匹配分</TableHead>
                    <TableHead className="w-28">风险</TableHead>
                    <TableHead className="min-w-56">摘要</TableHead>
                    <TableHead className="w-24">状态</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {items.map((item, index) => (
                    <TableRow key={item.resume_id}>
                      <TableCell className="font-mono text-slate-500">{index + 1}</TableCell>
                      <TableCell className="max-w-56">
                        <p className="truncate font-medium text-slate-900" title={item.resume_title}>
                          {item.resume_title}
                        </p>
                        {item.report_id ? (
                          <Link
                            href={`/report/${item.report_id}`}
                            className="text-xs text-brand-600 hover:underline"
                          >
                            查看报告 →
                          </Link>
                        ) : (
                          <span className="text-xs text-slate-400">报告生成中</span>
                        )}
                      </TableCell>
                      <TableCell className="font-mono text-slate-900">
                        {item.overall_score ?? '—'}
                      </TableCell>
                      <TableCell className="font-mono text-slate-700">
                        {item.match_score ?? '—'}
                      </TableCell>
                      <TableCell>
                        <RiskBadge level={item.risk_level} />
                      </TableCell>
                      <TableCell className="text-xs leading-relaxed text-slate-600">
                        {item.summary || '—'}
                      </TableCell>
                      <TableCell className="text-xs text-slate-500">
                        {ITEM_STATUS_TEXT[item.status] ?? item.status}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}

            <p className="text-xs text-slate-400">
              提示：分数只代表与当前 JD 文本的匹配程度，不代表候选人能力上限；请结合面试与业务需要综合判断。
            </p>
          </CardContent>
        </Card>
      ) : (
        <EmptyState
          title="还没有初筛批次"
          description="填写 JD、选择简历并点击「启动初筛」，结果会以表格形式按总分降序展示。"
        />
      )}
    </div>
  );
}
