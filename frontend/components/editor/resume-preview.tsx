'use client';

import * as React from 'react';
import type { ReactNode } from 'react';
import type { EditorContent } from '@/lib/types';
import { countFilledSections, SECTION_TITLES } from '@/lib/resume-content';
import { cn } from '@/lib/utils';
import { TemplateResume, type TemplateDesignId } from '@/components/editor/template-resume';

/**
 * 简历实时预览：A4 纸感的白色卡片，按后端导出顺序渲染，空区块不渲染。
 * 打印（Ctrl+P）时只保留本卡片（见 app/globals.css 与 `print:` 变体）。
 *
 * 传了 `design`（celadon / navy / ivory / cobalt）时，复用同一个 A4Frame
 * 渲染真实版式 `<TemplateResume>`；不传则完全保留旧的 ATS 单栏预览逻辑。
 */
export interface ResumePreviewProps {
  content: EditorContent;
  className?: string;
  /** 选定版式设计；缺省时走旧版 ATS 单栏预览 */
  design?: TemplateDesignId;
  /**
   * 没有头像时是否显示「照片位」示例框。默认 true：编辑器屏幕预览里让用户看到
   * 照片放在哪里；打印/导出时由 `.avatar-placeholder.print:hidden` 自动隐藏。
   */
  avatarPlaceholder?: boolean;
}

function Placeholder({ children }: { children: ReactNode }) {
  return (
    <span className="text-slate-400 print:hidden">{children}</span>
  );
}

function Bullets({ items }: { items: string[] }) {
  const values = items.map((item) => item.trim()).filter(Boolean);
  if (values.length === 0) return null;
  return (
    <ul className="mt-1.5 space-y-1">
      {values.map((value, index) => (
        <li key={index} className="flex gap-2 text-[13px] leading-relaxed text-slate-700">
          <span className="mt-[7px] h-1.5 w-1.5 shrink-0 rounded-full bg-slate-400" aria-hidden />
          <span className="break-words">{value}</span>
        </li>
      ))}
    </ul>
  );
}

function Chips({ items }: { items: string[] }) {
  const values = items.map((item) => item.trim()).filter(Boolean);
  if (values.length === 0) return null;
  return (
    <div className="flex flex-wrap gap-1.5">
      {values.map((value, index) => (
        <span
          key={index}
          className="rounded-md bg-slate-100 px-2 py-0.5 text-xs text-slate-700 print:bg-transparent print:px-0"
        >
          {value}
        </span>
      ))}
    </div>
  );
}

function SectionBlock({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="mt-6 break-inside-avoid">
      <h2 className="border-b border-slate-300 pb-1 text-[15px] font-semibold tracking-wide text-slate-900">
        {title}
      </h2>
      <div className="mt-3 space-y-3">{children}</div>
    </section>
  );
}

function ItemHeading({
  title,
  meta,
  hint,
}: {
  title: ReactNode;
  meta?: string;
  hint?: string;
}) {
  return (
    <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5">
      <p className="text-sm font-semibold text-slate-900">
        {title}
        {hint ? <Placeholder>{hint}</Placeholder> : null}
      </p>
      {meta ? <p className="text-xs text-slate-500">{meta}</p> : null}
    </div>
  );
}

function joinRange(start: string, end: string): string {
  const from = start.trim();
  const to = end.trim();
  if (from && to) return `${from} - ${to}`;
  return from || to;
}

const A4_WIDTH_PX = 794; // 210mm @ 96dpi
const A4_HEIGHT_PX = 1123; // 297mm @ 96dpi

/**
 * A4 预览容器（方案 1）：按 794px 的 A4 宽度排版，再等比缩放到容器宽度；
 * 每个 297mm 高度画一条分页参考线。分页**仅供参考** —— 真正的分页由导出时的
 * Word/LibreOffice 排版决定，这里不假装精确。
 */
function A4Frame({ children }: { children: ReactNode }) {
  const outerRef = React.useRef<HTMLDivElement>(null);
  const innerRef = React.useRef<HTMLDivElement>(null);
  const [scale, setScale] = React.useState(1);
  const [height, setHeight] = React.useState(A4_HEIGHT_PX);

  React.useEffect(() => {
    const update = () => {
      const outer = outerRef.current;
      const inner = innerRef.current;
      if (!outer || !inner) return;
      setScale(Math.min(1, Math.max(0.25, outer.clientWidth / A4_WIDTH_PX)));
      setHeight(Math.max(A4_HEIGHT_PX, inner.scrollHeight));
    };
    update();
    const observer = new ResizeObserver(update);
    if (outerRef.current) observer.observe(outerRef.current);
    if (innerRef.current) observer.observe(innerRef.current);
    return () => observer.disconnect();
  }, []);

  const pageCount = Math.max(1, Math.ceil(height / A4_HEIGHT_PX));

  return (
    <div className="w-full">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2 text-[10.5px] text-slate-400 print:hidden">
        <span>
          A4 预览 · 共 {pageCount} 页（分页仅供参考，以导出的 PDF 为准）
        </span>
        <span>导出时可勾选「压缩到一页」</span>
      </div>
      <div ref={outerRef} className="a4-frame w-full overflow-hidden">
        <div style={{ height: height * scale }} className="a4-frame-height relative">
          <div
            ref={innerRef}
            className="a4-frame-inner"
            style={{ width: A4_WIDTH_PX, transform: `scale(${scale})`, transformOrigin: 'top left' }}
          >
            {children}
          </div>
        </div>
      </div>
    </div>
  );
}

export function ResumePreview({
  content,
  className,
  design,
  avatarPlaceholder = true,
}: ResumePreviewProps) {
  // 选定版式：沿用 A4Frame 的缩放 / 分页线，只把内容换成真实版式组件。
  if (design) {
    return (
      <div className={cn('w-full', className)}>
        <A4Frame>
          <TemplateResume
            content={content}
            design={design}
            avatarPlaceholder={avatarPlaceholder}
          />
        </A4Frame>
      </div>
    );
  }

  const basics = content.basics;
  const contact = [basics.phone, basics.email, basics.city, ...basics.links]
    .map((value) => value.trim())
    .filter(Boolean);
  const filled = countFilledSections(content);
  const customSections = content.custom_sections.filter(
    (section) => section.title.trim() !== '' || section.items.some((item) => item.trim() !== ''),
  );

  return (
    <div className={cn('w-full', className)}>
      <A4Frame>
        <article
          className="relative mx-auto bg-white shadow-sm print:shadow-none"
          style={{ width: A4_WIDTH_PX, minHeight: A4_HEIGHT_PX, padding: '68px 76px' }}
        >
          {/* 分页参考线：每 297mm 一条虚线（打印时隐藏，交给浏览器分页） */}
          <div
            aria-hidden
            className="pointer-events-none absolute inset-0 print:hidden"
            style={{
              backgroundImage:
                'repeating-linear-gradient(to bottom, transparent 0, transparent calc(297mm - 1px), #cbd5e1 calc(297mm - 1px), #cbd5e1 297mm)',
            }}
          />
        {filled === 0 ? (
          <p className="mb-6 rounded-lg bg-slate-50 px-3 py-2 text-xs text-slate-500 print:hidden">
            左侧还没有填写内容，这里会实时生成你的简历预览。
          </p>
        ) : null}

        {/* 个人信息 */}
        <header className="flex items-start justify-between gap-6 break-inside-avoid">
          <div className="min-w-0">
            <h1 className="text-2xl font-semibold tracking-tight text-slate-900">
              {basics.name.trim() || <Placeholder>你的姓名</Placeholder>}
            </h1>
            {basics.target_title.trim() ? (
              <p className="mt-0.5 text-sm font-medium text-brand-700">{basics.target_title.trim()}</p>
            ) : null}
            {contact.length > 0 ? (
              <p className="mt-2 text-xs text-slate-600">{contact.join(' · ')}</p>
            ) : (
              <p className="mt-2 text-xs">
                <Placeholder>手机 / 邮箱 / 城市 / 作品集链接</Placeholder>
              </p>
            )}
          </div>
          {basics.avatar ? (
            // eslint-disable-next-line @next/next/no-img-element -- data URL 头像，next/img 不适用
            <img
              src={basics.avatar}
              alt="头像"
              className="h-[88px] w-[70px] shrink-0 border border-slate-200 object-cover"
            />
          ) : null}
        </header>

        {content.summary.trim() ? (
          <SectionBlock title={SECTION_TITLES.summary}>
            <p className="whitespace-pre-line text-[13px] leading-relaxed text-slate-700">
              {content.summary.trim()}
            </p>
          </SectionBlock>
        ) : null}

        {content.work.length > 0 ? (
          <SectionBlock title={SECTION_TITLES.work}>
            {content.work.map((item, index) => (
              <div key={index} className="break-inside-avoid">
                <ItemHeading
                  title={
                    [item.company.trim(), item.title.trim()].filter(Boolean).join(' | ') || null
                  }
                  meta={joinRange(item.start, item.end)}
                  hint={
                    [item.company, item.title].every((value) => !value.trim())
                      ? '公司 | 岗位'
                      : undefined
                  }
                />
                {item.location.trim() ? (
                  <p className="mt-0.5 text-xs text-slate-500">{item.location.trim()}</p>
                ) : null}
                <Bullets items={item.highlights} />
              </div>
            ))}
          </SectionBlock>
        ) : null}

        {content.projects.length > 0 ? (
          <SectionBlock title={SECTION_TITLES.projects}>
            {content.projects.map((item, index) => {
              const tech = item.tech.map((t) => t.trim()).filter(Boolean).join('、');
              const meta = [joinRange(item.start, item.end), tech].filter(Boolean).join(' · ');
              return (
                <div key={index} className="break-inside-avoid">
                  <ItemHeading
                    title={[item.name.trim(), item.role.trim()].filter(Boolean).join(' | ') || null}
                    meta={meta}
                    hint={
                      [item.name, item.role].every((value) => !value.trim())
                        ? '项目名称 | 担任角色'
                        : undefined
                    }
                  />
                  <Bullets items={item.highlights} />
                </div>
              );
            })}
          </SectionBlock>
        ) : null}

        {content.education.length > 0 ? (
          <SectionBlock title={SECTION_TITLES.education}>
            {content.education.map((item, index) => (
              <div key={index} className="break-inside-avoid">
                <ItemHeading
                  title={
                    [item.school.trim(), item.degree.trim(), item.major.trim()]
                      .filter(Boolean)
                      .join(' | ') || null
                  }
                  meta={joinRange(item.start, item.end)}
                  hint={
                    [item.school, item.degree, item.major].every((value) => !value.trim())
                      ? '学校 | 学历 | 专业'
                      : undefined
                  }
                />
              </div>
            ))}
          </SectionBlock>
        ) : null}

        {content.skills.some((item) => item.trim() !== '') ? (
          <SectionBlock title={SECTION_TITLES.skills}>
            <Chips items={content.skills} />
          </SectionBlock>
        ) : null}

        {content.certificates.some((item) => item.trim() !== '') ? (
          <SectionBlock title={SECTION_TITLES.certificates}>
            <Bullets items={content.certificates} />
          </SectionBlock>
        ) : null}

        {content.awards.some((item) => item.trim() !== '') ? (
          <SectionBlock title={SECTION_TITLES.awards}>
            <Bullets items={content.awards} />
          </SectionBlock>
        ) : null}

        {customSections.map((section, index) => (
          <SectionBlock key={index} title={section.title.trim() || SECTION_TITLES.custom}>
            <Bullets items={section.items} />
          </SectionBlock>
        ))}
        </article>
      </A4Frame>
    </div>
  );
}

export default ResumePreview;