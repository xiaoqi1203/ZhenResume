'use client';

/**
 * 真实版式简历（第一批 4 套）：青简细线 / 深蓝序章 / 象牙书页 / 钴蓝横帆。
 *
 * 设计原则
 * --------
 * 1. **一份内容、四种视觉**：`TemplateResume` 负责把 `EditorContent` 排成真实 A4 纸面
 *    （794 × 1123px，即 210 × 297mm @96dpi），accent 颜色全部走内联 style，
 *    不依赖动态 Tailwind 类名，也不引入任何新依赖 / 图片。
 * 2. **屏幕与打印同源**：页面根节点带 `print-color-adjust: exact`，配合
 *    `app/globals.css` 的 @media print，浏览器另存 PDF 时色块与hairline 不会被吃掉。
 * 3. **`ScaledTemplatePreview`** 是同一版式的等比缩略图，供模板画廊卡片 / 详情大图复用。
 *
 * 空区块不渲染：没有内容的分区（summary/work/…/custom_sections）整段不出现，
 * 空条目也被过滤，避免预览里出现只有标题的空壳。
 */

import * as React from 'react';
import type { CSSProperties, ReactNode } from 'react';

import { SECTION_TITLES } from '@/lib/resume-content';
import type { EditorContent } from '@/lib/types';
import { cn } from '@/lib/utils';

/** 第一批 4 套版式；后端模板元数据里的 `design` 字段与此一一对应。 */
export type TemplateDesignId = 'celadon' | 'navy' | 'ivory' | 'cobalt';

const A4_WIDTH_PX = 794; // 210mm @ 96dpi
const A4_HEIGHT_PX = 1123; // 297mm @ 96dpi

const SANS_FONT =
  '"Helvetica Neue", Helvetica, "Segoe UI", "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "Source Han Sans SC", Arial, sans-serif';
/** 象牙书页用的中文衬线栈；Tailwind 的 font-serif 在本项目里被重映射成无衬线，故直接内联。 */
const SERIF_FONT =
  '"Songti SC", "STSong", "SimSun", "Source Han Serif SC", "Noto Serif SC", Georgia, "Times New Roman", serif';

interface DesignTokens {
  /** 纸面底色 */
  pageBg: string;
  /** 姓名 / 条目标题色 */
  ink: string;
  /** 正文色 */
  body: string;
  /** 日期、联系方式等次级文字 */
  muted: string;
  /** 主强调色（青绿 / 深蓝 / 象牙棕 / 钴蓝） */
  accent: string;
  /** 强调色的浅色底（chips 背景） */
  accentSoft: string;
  /** 强调色 hairline */
  accentBorder: string;
  fontFamily: string;
  headingFamily: string;
  padding: string;
  sectionGap: number;
  itemGap: number;
}

const DESIGNS: Record<TemplateDesignId, DesignTokens> = {
  celadon: {
    pageBg: '#FFFFFF',
    ink: '#1F2A26',
    body: '#3B4742',
    muted: '#7C8B84',
    accent: '#5D7F6B',
    accentSoft: '#F2F6F3',
    accentBorder: 'rgba(93, 127, 107, 0.34)',
    fontFamily: SANS_FONT,
    headingFamily: SANS_FONT,
    padding: '68px 76px 64px',
    sectionGap: 26,
    itemGap: 14,
  },
  navy: {
    pageBg: '#FFFFFF',
    ink: '#17324D',
    body: '#33475C',
    muted: '#70859A',
    accent: '#17324D',
    accentSoft: '#EEF3F7',
    accentBorder: 'rgba(23, 50, 77, 0.6)',
    fontFamily: SANS_FONT,
    headingFamily: SANS_FONT,
    padding: '92px 72px 64px',
    sectionGap: 24,
    itemGap: 14,
  },
  ivory: {
    pageBg: '#FAF6ED',
    ink: '#3B342B',
    body: '#4A4338',
    muted: '#8C7F6C',
    accent: '#B08A63',
    accentSoft: '#F1E8DA',
    accentBorder: 'rgba(176, 138, 99, 0.42)',
    fontFamily: SERIF_FONT,
    headingFamily: SERIF_FONT,
    padding: '62px 66px 58px',
    sectionGap: 30,
    itemGap: 14,
  },
  cobalt: {
    pageBg: '#FFFFFF',
    ink: '#16233D',
    body: '#33415A',
    muted: '#6E7C93',
    accent: '#1F4E9C',
    accentSoft: '#EDF2FA',
    accentBorder: 'rgba(31, 78, 156, 0.22)',
    fontFamily: SANS_FONT,
    headingFamily: SANS_FONT,
    padding: '0',
    sectionGap: 26,
    itemGap: 14,
  },
};

/* ------------------------------------------------------------------ */
/* 纯函数工具                                                          */
/* ------------------------------------------------------------------ */

function joinRange(start: string, end: string): string {
  const from = start.trim();
  const to = end.trim();
  if (from && to) return `${from} - ${to}`;
  return from || to;
}

function cleanList(values: string[]): string[] {
  return values.map((value) => value.trim()).filter(Boolean);
}

function hasAny(...values: string[]): boolean {
  return values.some((value) => value.trim().length > 0);
}

function contactValues(basics: EditorContent['basics']): string[] {
  return [basics.phone, basics.email, basics.city, ...basics.links]
    .map((value) => value.trim())
    .filter(Boolean);
}

/* ------------------------------------------------------------------ */
/* 头像                                                                */
/* ------------------------------------------------------------------ */

function Avatar({
  src,
  design,
  placeholder = false,
}: {
  src: string;
  design: TemplateDesignId;
  /** 没有上传头像时，是否显示「照片位」示例框（画廊缩略图/编辑器屏幕预览用，打印时隐藏） */
  placeholder?: boolean;
}) {
  const shapes: Record<TemplateDesignId, CSSProperties> = {
    celadon: { width: 64, height: 80, border: '1px solid rgba(93, 127, 107, 0.4)' },
    navy: { width: 72, height: 88, border: '2px solid #17324D' },
    ivory: {
      width: 78,
      height: 78,
      borderRadius: '50%',
      border: '1px solid rgba(176, 138, 99, 0.5)',
      margin: '0 auto 16px',
    },
    cobalt: { width: 86, height: 104, border: '2px solid rgba(255, 255, 255, 0.82)' },
  };

  if (!src && placeholder) {
    const accent = design === 'cobalt' ? 'rgba(255, 255, 255, 0.82)' : 'currentColor';
    return (
      <div
        className="avatar-placeholder print:hidden"
        aria-label="照片位"
        style={{
          ...shapes[design],
          display: 'flex',
          flexShrink: 0,
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          gap: 4,
          borderStyle: 'dashed',
          color:
            design === 'cobalt'
              ? 'rgba(255, 255, 255, 0.8)'
              : design === 'ivory'
                ? 'rgba(176, 138, 99, 0.9)'
                : design === 'navy'
                  ? 'rgba(23, 50, 77, 0.62)'
                  : 'rgba(93, 127, 107, 0.78)',
          backgroundColor: design === 'cobalt' ? 'rgba(255, 255, 255, 0.08)' : 'rgba(0, 0, 0, 0.015)',
        }}
      >
        <svg
          width={design === 'ivory' ? 22 : 20}
          height={design === 'ivory' ? 22 : 20}
          viewBox="0 0 24 24"
          fill="none"
          stroke={accent}
          strokeWidth={1.4}
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden
        >
          <circle cx="12" cy="8" r="3.2" />
          <path d="M4.5 20c.8-3.2 3.6-5 7.5-5s6.7 1.8 7.5 5" />
        </svg>
        <span style={{ fontSize: 9, letterSpacing: '0.18em', lineHeight: 1 }}>照片</span>
      </div>
    );
  }

  return (
    // eslint-disable-next-line @next/next/no-img-element -- 头像是 data URL，next/image 不适用
    <img
      src={src}
      alt="头像"
      style={{
        ...shapes[design],
        display: 'block',
        flexShrink: 0,
        objectFit: 'cover',
        backgroundColor: design === 'cobalt' ? 'rgba(255, 255, 255, 0.12)' : '#F4F4F4',
      }}
    />
  );
}

/* ------------------------------------------------------------------ */
/* 页码 / 条目 / chips 等基础块                                        */
/* ------------------------------------------------------------------ */

function bulletMarker(design: TemplateDesignId, tokens: DesignTokens): CSSProperties {
  switch (design) {
    case 'celadon':
      // 青简：一条极短的青色细线
      return { width: 9, height: 1, marginTop: 9, backgroundColor: tokens.accent };
    case 'navy':
      // 深蓝：小菱形
      return {
        width: 5,
        height: 5,
        marginTop: 7,
        backgroundColor: tokens.accent,
        transform: 'rotate(45deg)',
      };
    case 'ivory':
      // 象牙：小圆点
      return { width: 3, height: 3, marginTop: 9, borderRadius: '50%', backgroundColor: tokens.accent };
    case 'cobalt':
    default:
      // 钴蓝：实心小方块
      return { width: 4, height: 4, marginTop: 8, backgroundColor: tokens.accent };
  }
}

function Bullets({
  items,
  design,
  tokens,
}: {
  items: string[];
  design: TemplateDesignId;
  tokens: DesignTokens;
}) {
  const values = cleanList(items);
  if (values.length === 0) return null;
  const marker = bulletMarker(design, tokens);

  return (
    <ul
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: 4,
        margin: '7px 0 0',
        padding: 0,
        listStyle: 'none',
      }}
    >
      {values.map((value, index) => (
        <li
          key={index}
          style={{
            display: 'flex',
            gap: 8,
            fontSize: 12.5,
            lineHeight: 1.7,
            color: tokens.body,
            breakInside: 'avoid',
          }}
        >
          <span aria-hidden style={{ ...marker, flexShrink: 0 }} />
          <span style={{ flex: 1, minWidth: 0, wordBreak: 'break-word' }}>{value}</span>
        </li>
      ))}
    </ul>
  );
}

function Chips({
  items,
  design,
  tokens,
}: {
  items: string[];
  design: TemplateDesignId;
  tokens: DesignTokens;
}) {
  const values = cleanList(items);
  if (values.length === 0) return null;

  const chipStyle: CSSProperties =
    design === 'ivory'
      ? {
          border: '1px solid rgba(176, 138, 99, 0.45)',
          backgroundColor: '#F4ECDF',
          color: '#6B5C48',
          padding: '2.5px 9px',
          fontSize: 11.5,
        }
      : design === 'navy'
        ? {
            border: '1px solid rgba(23, 50, 77, 0.2)',
            borderLeft: '2px solid #17324D',
            backgroundColor: '#F5F8FA',
            color: '#17324D',
            padding: '2.5px 9px',
            fontSize: 11.5,
          }
        : design === 'cobalt'
          ? {
              border: '1px solid #D7E3F6',
              backgroundColor: '#EDF2FA',
              color: '#1F4E9C',
              padding: '2.5px 9px',
              fontSize: 11.5,
            }
          : {
              border: '1px solid rgba(93, 127, 107, 0.3)',
              backgroundColor: '#F2F6F3',
              color: '#3B4742',
              padding: '2.5px 9px',
              fontSize: 11.5,
            };

  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
      {values.map((value, index) => (
        <span key={index} style={{ ...chipStyle, lineHeight: 1.5, whiteSpace: 'normal' }}>
          {value}
        </span>
      ))}
    </div>
  );
}

function ItemHeading({
  title,
  meta,
  design,
  tokens,
}: {
  title: string;
  meta: string;
  design: TemplateDesignId;
  tokens: DesignTokens;
}) {
  if (!title && !meta) return null;
  const serif = design === 'ivory';

  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'baseline',
        justifyContent: 'space-between',
        gap: 14,
      }}
    >
      {title ? (
        <p
          style={{
            flex: 1,
            minWidth: 0,
            margin: 0,
            fontFamily: serif ? tokens.headingFamily : tokens.fontFamily,
            fontSize: serif ? 14 : 13.5,
            fontWeight: 600,
            letterSpacing: serif ? '0.04em' : '0.01em',
            lineHeight: 1.5,
            color: tokens.ink,
            wordBreak: 'break-word',
          }}
        >
          {title}
        </p>
      ) : (
        <span style={{ flex: 1 }} />
      )}
      {meta ? (
        <p
          style={{
            flexShrink: 0,
            margin: 0,
            fontSize: 11.5,
            lineHeight: 1.5,
            color: tokens.muted,
            fontVariantNumeric: 'tabular-nums',
            fontStyle: serif ? 'italic' : 'normal',
          }}
        >
          {meta}
        </p>
      ) : null}
    </div>
  );
}

/** 章节标题：4 套版式差异最集中的地方。 */
function SectionTitle({
  title,
  design,
  tokens,
}: {
  title: string;
  design: TemplateDesignId;
  tokens: DesignTokens;
}) {
  if (design === 'celadon') {
    return (
      <h2
        style={{
          margin: 0,
          padding: '7px 0 6px',
          borderTop: `1px solid ${tokens.accentBorder}`,
          borderBottom: `1px solid ${tokens.accentBorder}`,
          fontFamily: tokens.headingFamily,
          fontSize: 12,
          fontWeight: 600,
          letterSpacing: '0.24em',
          lineHeight: 1.4,
          color: tokens.accent,
        }}
      >
        {title}
      </h2>
    );
  }

  if (design === 'navy') {
    return (
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <span aria-hidden style={{ width: 3, height: 16, backgroundColor: tokens.accent }} />
        <h2
          style={{
            margin: 0,
            fontFamily: tokens.headingFamily,
            fontSize: 14.5,
            fontWeight: 700,
            letterSpacing: '0.04em',
            lineHeight: 1.4,
            color: tokens.accent,
          }}
        >
          {title}
        </h2>
        <span aria-hidden style={{ flex: 1, height: 1, backgroundColor: 'rgba(23, 50, 77, 0.14)' }} />
      </div>
    );
  }

  if (design === 'ivory') {
    return (
      <h2
        style={{
          margin: 0,
          padding: '6px 0 7px',
          borderTop: `3px double ${tokens.accentBorder}`,
          borderBottom: `3px double ${tokens.accentBorder}`,
          textAlign: 'center',
          fontFamily: tokens.headingFamily,
          fontSize: 14,
          fontWeight: 600,
          letterSpacing: '0.34em',
          lineHeight: 1.3,
          color: tokens.ink,
        }}
      >
        {title}
      </h2>
    );
  }

  // cobalt
  return (
    <h2
      style={{
        margin: 0,
        paddingBottom: 6,
        borderBottom: `1px solid ${tokens.accentBorder}`,
        fontFamily: tokens.headingFamily,
        fontSize: 14.5,
        fontWeight: 700,
        letterSpacing: '0.12em',
        lineHeight: 1.4,
        color: tokens.accent,
      }}
    >
      {title}
    </h2>
  );
}

function Section({
  title,
  design,
  tokens,
  children,
}: {
  title: string;
  design: TemplateDesignId;
  tokens: DesignTokens;
  children: ReactNode;
}) {
  return (
    <section style={{ marginTop: tokens.sectionGap }}>
      <div style={{ breakInside: 'avoid' }}>
        <SectionTitle title={title} design={design} tokens={tokens} />
      </div>
      <div
        style={{
          display: 'flex',
          flexDirection: 'column',
          gap: tokens.itemGap,
          marginTop: design === 'ivory' ? 14 : 12,
        }}
      >
        {children}
      </div>
    </section>
  );
}

/* ------------------------------------------------------------------ */
/* 页首：4 套版式的“第一眼差异”                                        */
/* ------------------------------------------------------------------ */

function TemplateHeader({
  content,
  design,
  tokens,
  avatarPlaceholder = false,
}: {
  content: EditorContent;
  design: TemplateDesignId;
  tokens: DesignTokens;
  avatarPlaceholder?: boolean;
}) {
  const basics = content.basics;
  const name = basics.name.trim();
  const target = (basics.target_title ?? '').trim();
  const contact = contactValues(basics);
  const avatar = (basics.avatar ?? '').trim();
  const hasBasics = Boolean(name || target || contact.length > 0 || avatar);
  // 只有已经填写了姓名/岗位/联系方式（或上传了头像）才显示页首；
  // 空内容时不能因为“照片位”而渲染一个空页首。
  if (!hasBasics) return null;

  if (design === 'cobalt') {
    return (
      <header
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: 32,
          padding: '40px 64px',
          backgroundColor: tokens.accent,
          color: '#FFFFFF',
          borderBottom: '4px solid #163B78',
        }}
      >
        <div style={{ minWidth: 0 }}>
          {name ? (
            <h1
              style={{
                margin: 0,
                fontFamily: tokens.headingFamily,
                fontSize: 30,
                fontWeight: 700,
                letterSpacing: '0.02em',
                lineHeight: 1.25,
                color: '#FFFFFF',
              }}
            >
              {name}
            </h1>
          ) : null}
          {target ? (
            <p
              style={{
                margin: '8px 0 0',
                fontSize: 12,
                fontWeight: 500,
                letterSpacing: '0.22em',
                lineHeight: 1.5,
                color: 'rgba(255, 255, 255, 0.82)',
              }}
            >
              {target}
            </p>
          ) : null}
          {contact.length > 0 ? (
            <p
              style={{
                margin: '12px 0 0',
                fontSize: 11.5,
                lineHeight: 1.7,
                color: 'rgba(255, 255, 255, 0.88)',
              }}
            >
              {contact.join('   |   ')}
            </p>
          ) : null}
        </div>
        {avatar || avatarPlaceholder ? (
          <Avatar src={avatar} design="cobalt" placeholder={avatarPlaceholder} />
        ) : null}
      </header>
    );
  }

  if (design === 'navy') {
    return (
      <header
        style={{
          position: 'relative',
          zIndex: 1,
          display: 'flex',
          alignItems: 'flex-start',
          justifyContent: 'space-between',
          gap: 28,
          paddingBottom: 22,
          borderBottom: '1px solid rgba(23, 50, 77, 0.14)',
        }}
      >
        <div style={{ minWidth: 0 }}>
          {name ? (
            <h1
              style={{
                margin: 0,
                fontFamily: tokens.headingFamily,
                fontSize: 32,
                fontWeight: 700,
                letterSpacing: '-0.01em',
                lineHeight: 1.2,
                color: tokens.accent,
              }}
            >
              {name}
            </h1>
          ) : null}
          {target ? (
            <p
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 8,
                margin: '8px 0 0',
                fontSize: 12,
                fontWeight: 500,
                letterSpacing: '0.2em',
                lineHeight: 1.5,
                color: tokens.muted,
              }}
            >
              <span aria-hidden style={{ width: 16, height: 2, backgroundColor: tokens.accent }} />
              {target}
            </p>
          ) : null}
          {contact.length > 0 ? (
            <p
              style={{
                margin: '12px 0 0',
                fontSize: 11.5,
                lineHeight: 1.7,
                color: tokens.muted,
              }}
            >
              {contact.join('  ·  ')}
            </p>
          ) : null}
        </div>
        {avatar || avatarPlaceholder ? (
          <Avatar src={avatar} design="navy" placeholder={avatarPlaceholder} />
        ) : null}
      </header>
    );
  }

  if (design === 'ivory') {
    return (
      <header style={{ textAlign: 'center' }}>
        {avatar || avatarPlaceholder ? (
          <Avatar src={avatar} design="ivory" placeholder={avatarPlaceholder} />
        ) : null}
        {name ? (
          <h1
            style={{
              margin: 0,
              fontFamily: tokens.headingFamily,
              fontSize: 34,
              fontWeight: 500,
              letterSpacing: '0.18em',
              lineHeight: 1.25,
              color: tokens.ink,
            }}
          >
            {name}
          </h1>
        ) : null}
        {target ? (
          <p
            style={{
              margin: '9px 0 0',
              fontFamily: tokens.headingFamily,
              fontSize: 13,
              letterSpacing: '0.32em',
              lineHeight: 1.5,
              color: tokens.accent,
            }}
          >
            {target}
          </p>
        ) : null}
        {contact.length > 0 ? (
          <p
            style={{
              margin: '13px 0 0',
              fontFamily: tokens.headingFamily,
              fontSize: 11.5,
              letterSpacing: '0.04em',
              lineHeight: 1.7,
              color: tokens.muted,
            }}
          >
            {contact.join('   ·   ')}
          </p>
        ) : null}
        <div
          aria-hidden
          style={{
            height: 4,
            marginTop: 20,
            borderTop: `1px solid ${tokens.accentBorder}`,
            borderBottom: `1px solid ${tokens.accentBorder}`,
          }}
        />
      </header>
    );
  }

  // celadon
  return (
    <header
      style={{
        display: 'flex',
        alignItems: 'flex-start',
        justifyContent: 'space-between',
        gap: 28,
        paddingBottom: 22,
        borderBottom: '1px solid rgba(93, 127, 107, 0.32)',
      }}
    >
      <div style={{ minWidth: 0 }}>
        {name ? (
          <h1
            style={{
              margin: 0,
              fontFamily: tokens.headingFamily,
              fontSize: 31,
              fontWeight: 600,
              letterSpacing: '0.01em',
              lineHeight: 1.2,
              color: tokens.ink,
            }}
          >
            {name}
          </h1>
        ) : null}
        {target ? (
          <p
            style={{
              margin: '7px 0 0',
              fontSize: 13,
              fontWeight: 500,
              letterSpacing: '0.16em',
              lineHeight: 1.5,
              color: tokens.accent,
            }}
          >
            {target}
          </p>
        ) : null}
        {contact.length > 0 ? (
          <p
            style={{
              margin: '11px 0 0',
              fontSize: 11.5,
              lineHeight: 1.7,
              color: tokens.muted,
            }}
          >
            {contact.join('  ·  ')}
          </p>
        ) : null}
      </div>
      {avatar || avatarPlaceholder ? (
        <Avatar src={avatar} design="celadon" placeholder={avatarPlaceholder} />
      ) : null}
    </header>
  );
}

/* ------------------------------------------------------------------ */
/* 正文区块                                                            */
/* ------------------------------------------------------------------ */

function TemplateSections({
  content,
  design,
  tokens,
}: {
  content: EditorContent;
  design: TemplateDesignId;
  tokens: DesignTokens;
}) {
  const summary = content.summary.trim();
  const work = content.work.filter(
    (item) =>
      hasAny(item.company, item.title, item.start, item.end, item.location) ||
      cleanList(item.highlights).length > 0,
  );
  const projects = content.projects.filter(
    (item) =>
      hasAny(item.name, item.role, item.start, item.end) ||
      cleanList(item.tech).length > 0 ||
      cleanList(item.highlights).length > 0,
  );
  const education = content.education.filter((item) =>
    hasAny(item.school, item.degree, item.major, item.start, item.end),
  );
  const skills = cleanList(content.skills);
  const certificates = cleanList(content.certificates);
  const awards = cleanList(content.awards);
  const customSections = content.custom_sections.filter(
    (section) => section.title.trim() !== '' || cleanList(section.items).length > 0,
  );

  const bodyText: CSSProperties = {
    margin: 0,
    fontSize: 12.5,
    lineHeight: 1.85,
    color: tokens.body,
    whiteSpace: 'pre-line',
  };

  return (
    <>
      {summary ? (
        <Section title={SECTION_TITLES.summary} design={design} tokens={tokens}>
          <p style={bodyText}>{summary}</p>
        </Section>
      ) : null}

      {work.length > 0 ? (
        <Section title={SECTION_TITLES.work} design={design} tokens={tokens}>
          {work.map((item, index) => {
            const title = [item.company.trim(), item.title.trim()].filter(Boolean).join(' | ');
            return (
              <div key={index} style={{ breakInside: 'avoid' }}>
                <ItemHeading
                  title={title}
                  meta={joinRange(item.start, item.end)}
                  design={design}
                  tokens={tokens}
                />
                {item.location.trim() ? (
                  <p style={{ margin: '3px 0 0', fontSize: 11.5, lineHeight: 1.6, color: tokens.muted }}>
                    {item.location.trim()}
                  </p>
                ) : null}
                <Bullets items={item.highlights} design={design} tokens={tokens} />
              </div>
            );
          })}
        </Section>
      ) : null}

      {projects.length > 0 ? (
        <Section title={SECTION_TITLES.projects} design={design} tokens={tokens}>
          {projects.map((item, index) => {
            const title = [item.name.trim(), item.role.trim()].filter(Boolean).join(' | ');
            const tech = cleanList(item.tech).join('、');
            return (
              <div key={index} style={{ breakInside: 'avoid' }}>
                <ItemHeading
                  title={title}
                  meta={joinRange(item.start, item.end)}
                  design={design}
                  tokens={tokens}
                />
                {tech ? (
                  <p
                    style={{
                      margin: '3px 0 0',
                      fontSize: 11.5,
                      lineHeight: 1.6,
                      color: tokens.accent,
                      letterSpacing: '0.02em',
                    }}
                  >
                    {tech}
                  </p>
                ) : null}
                <Bullets items={item.highlights} design={design} tokens={tokens} />
              </div>
            );
          })}
        </Section>
      ) : null}

      {education.length > 0 ? (
        <Section title={SECTION_TITLES.education} design={design} tokens={tokens}>
          {education.map((item, index) => {
            const title = [item.school.trim(), item.degree.trim(), item.major.trim()]
              .filter(Boolean)
              .join(' | ');
            return (
              <div key={index} style={{ breakInside: 'avoid' }}>
                <ItemHeading
                  title={title}
                  meta={joinRange(item.start, item.end)}
                  design={design}
                  tokens={tokens}
                />
              </div>
            );
          })}
        </Section>
      ) : null}

      {skills.length > 0 ? (
        <Section title={SECTION_TITLES.skills} design={design} tokens={tokens}>
          <Chips items={skills} design={design} tokens={tokens} />
        </Section>
      ) : null}

      {certificates.length > 0 ? (
        <Section title={SECTION_TITLES.certificates} design={design} tokens={tokens}>
          <Bullets items={certificates} design={design} tokens={tokens} />
        </Section>
      ) : null}

      {awards.length > 0 ? (
        <Section title={SECTION_TITLES.awards} design={design} tokens={tokens}>
          <Bullets items={awards} design={design} tokens={tokens} />
        </Section>
      ) : null}

      {customSections.map((section, index) => {
        const values = cleanList(section.items);
        return (
          <Section
            key={index}
            title={section.title.trim() || SECTION_TITLES.custom}
            design={design}
            tokens={tokens}
          >
            <Bullets items={values} design={design} tokens={tokens} />
          </Section>
        );
      })}
    </>
  );
}

/* ------------------------------------------------------------------ */
/* 对外组件                                                            */
/* ------------------------------------------------------------------ */

export interface TemplateResumeProps {
  content: EditorContent;
  design?: TemplateDesignId;
  className?: string;
  /** 没有头像时显示「照片位」示例框（屏幕预览用；打印时自动隐藏） */
  avatarPlaceholder?: boolean;
}

/**
 * 完整 A4 版式简历（width 794px / min-height 1123px）。
 * 供编辑器实时预览与浏览器打印（Ctrl+P / 另存为 PDF）共用。
 */
export function TemplateResume({
  content,
  design = 'celadon',
  className,
  avatarPlaceholder = false,
}: TemplateResumeProps) {
  // 后端模板元数据里可能有本批之外的 design（例如老模板的映射值），
  // 这里统一回落到 celadon，避免页面拿到未定义的 token。
  const safeDesign: TemplateDesignId = DESIGNS[design] ? design : 'celadon';
  const tokens = DESIGNS[safeDesign];
  const isCobalt = safeDesign === 'cobalt';
  const isIvory = safeDesign === 'ivory';

  return (
    <article
      data-template-design={safeDesign}
      className={cn('template-resume', className)}
      style={{
        position: 'relative',
        width: A4_WIDTH_PX,
        minHeight: A4_HEIGHT_PX,
        boxSizing: 'border-box',
        overflow: 'hidden',
        backgroundColor: tokens.pageBg,
        color: tokens.body,
        fontFamily: tokens.fontFamily,
        // 打印时保留页首色块 / 背景 / hairline
        colorAdjust: 'exact',
        printColorAdjust: 'exact',
        WebkitPrintColorAdjust: 'exact',
      }}
    >
      {isIvory ? (
        <div
          aria-hidden
          style={{
            position: 'absolute',
            inset: 20,
            border: '1px solid rgba(176, 138, 99, 0.22)',
            pointerEvents: 'none',
          }}
        />
      ) : null}

      {isCobalt ? (
        <>
          <TemplateHeader
            content={content}
            design={safeDesign}
            tokens={tokens}
            avatarPlaceholder={avatarPlaceholder}
          />
          <div style={{ padding: '46px 64px 64px' }}>
            <TemplateSections content={content} design={safeDesign} tokens={tokens} />
          </div>
        </>
      ) : (
        <div style={{ position: 'relative', zIndex: 1, padding: tokens.padding }}>
          <TemplateHeader
            content={content}
            design={safeDesign}
            tokens={tokens}
            avatarPlaceholder={avatarPlaceholder}
          />
          <TemplateSections content={content} design={safeDesign} tokens={tokens} />
        </div>
      )}
    </article>
  );
}

export interface ScaledTemplatePreviewProps {
  content: EditorContent;
  design?: TemplateDesignId;
  className?: string;
  avatarPlaceholder?: boolean;
}

/**
 * 等比缩略图：外层固定 210 / 297（A4）比例并裁切，内层把 794px 版式缩放到容器宽度。
 * 模板画廊的卡片缩略图与详情大图共用，真实内容渲染，不用截图。
 */
export function ScaledTemplatePreview({
  content,
  design = 'celadon',
  className,
  avatarPlaceholder = false,
}: ScaledTemplatePreviewProps) {
  const outerRef = React.useRef<HTMLDivElement>(null);
  const [scale, setScale] = React.useState(0);

  React.useEffect(() => {
    const outer = outerRef.current;
    if (!outer) return;

    const update = () => {
      const width = outer.clientWidth;
      if (width > 0) setScale(width / A4_WIDTH_PX);
    };

    update();
    const observer = new ResizeObserver(update);
    observer.observe(outer);
    return () => observer.disconnect();
  }, []);

  return (
    <div
      ref={outerRef}
      className={cn('relative overflow-hidden border border-ink-200 bg-white', className)}
      style={{
        width: '100%',
        aspectRatio: '210 / 297',
        // 缩略图不参与交互（画廊卡片自身承担点击）
        pointerEvents: 'none',
      }}
      aria-hidden
    >
      {scale > 0 ? (
        <div
          style={{
            position: 'absolute',
            top: 0,
            left: 0,
            width: A4_WIDTH_PX,
            transform: `scale(${scale})`,
            transformOrigin: 'top left',
          }}
        >
          <TemplateResume content={content} design={design} avatarPlaceholder={avatarPlaceholder} />
        </div>
      ) : null}
    </div>
  );
}

export default TemplateResume;