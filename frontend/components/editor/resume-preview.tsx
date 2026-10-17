import type { ReactNode } from 'react';
import type { EditorContent } from '@/lib/types';
import { countFilledSections, SECTION_TITLES } from '@/lib/resume-content';
import { cn } from '@/lib/utils';

/**
 * 简历实时预览：A4 纸感的白色卡片，按后端导出顺序渲染，空区块不渲染。
 * 打印（Ctrl+P）时只保留本卡片（见 app/globals.css 与 `print:` 变体）。
 */
export interface ResumePreviewProps {
  content: EditorContent;
  className?: string;
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

export function ResumePreview({ content, className }: ResumePreviewProps) {
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
      <article className="mx-auto w-full max-w-[820px] rounded-xl border border-slate-200 bg-white px-6 py-8 shadow-sm sm:px-10 sm:py-12 print:max-w-none print:rounded-none print:border-0 print:p-0 print:shadow-none">
        {filled === 0 ? (
          <p className="mb-6 rounded-lg bg-slate-50 px-3 py-2 text-xs text-slate-500 print:hidden">
            左侧还没有填写内容，这里会实时生成你的简历预览。
          </p>
        ) : null}

        {/* 个人信息 */}
        <header className="break-inside-avoid">
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
    </div>
  );
}

export default ResumePreview;