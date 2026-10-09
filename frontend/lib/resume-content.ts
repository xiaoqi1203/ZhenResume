/**
 * 结构化简历内容（契约 §2.5 EditorContent）的纯函数工具：
 * 归一化、ParsedResume 映射、完成度统计、Markdown 渲染。
 *
 * 渲染顺序与后端保持一致：
 * 个人信息 → 个人优势 → 工作经历 → 项目经历 → 教育经历 → 专业技能 → 证书 → 荣誉奖项 → 自定义区块。
 */

import type {
  EditorContent,
  EditorEducationItem,
  EditorProjectItem,
  EditorWorkItem,
  ParsedResume,
  TemplateSectionKey,
} from './types';

/** 区块顺序（与后端渲染顺序一致，`custom` 对应 `custom_sections`）。 */
export const EDITOR_SECTION_KEYS: readonly TemplateSectionKey[] = [
  'basics',
  'summary',
  'work',
  'projects',
  'education',
  'skills',
  'certificates',
  'awards',
  'custom',
];

export const SECTION_TITLES: Record<TemplateSectionKey, string> = {
  basics: '个人信息',
  summary: '个人优势',
  work: '工作经历',
  projects: '项目经历',
  education: '教育经历',
  skills: '专业技能',
  certificates: '证书',
  awards: '荣誉奖项',
  custom: '自定义区块',
};

/** 空着会明显影响诊断质量的关键区块（只做轻提示，不阻塞提交）。 */
export const KEY_SECTION_KEYS: readonly TemplateSectionKey[] = ['basics', 'summary', 'work'];

/** 区块总数（完成度分母）。 */
export const SECTION_TOTAL = EDITOR_SECTION_KEYS.length;

function asString(value: unknown): string {
  return typeof value === 'string' ? value : '';
}

function asStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is string => typeof item === 'string');
}

function hasText(...values: string[]): boolean {
  return values.some((value) => value.trim().length > 0);
}

/** 把后端返回值补齐成完整的 EditorContent（缺键、脏数据都不会让页面崩）。 */
export function normalizeEditorContent(input: unknown): EditorContent {
  const source = (input ?? {}) as Record<string, unknown>;
  const basics = (source.basics ?? {}) as Record<string, unknown>;

  const work: EditorWorkItem[] = (Array.isArray(source.work) ? source.work : []).map((raw) => {
    const item = (raw ?? {}) as Record<string, unknown>;
    return {
      company: asString(item.company),
      title: asString(item.title),
      start: asString(item.start),
      end: asString(item.end),
      location: asString(item.location),
      highlights: asStringArray(item.highlights),
    };
  });

  const projects: EditorProjectItem[] = (Array.isArray(source.projects) ? source.projects : []).map(
    (raw) => {
      const item = (raw ?? {}) as Record<string, unknown>;
      return {
        name: asString(item.name),
        role: asString(item.role),
        start: asString(item.start),
        end: asString(item.end),
        tech: asStringArray(item.tech),
        highlights: asStringArray(item.highlights),
      };
    },
  );

  const education: EditorEducationItem[] = (
    Array.isArray(source.education) ? source.education : []
  ).map((raw) => {
    const item = (raw ?? {}) as Record<string, unknown>;
    return {
      school: asString(item.school),
      degree: asString(item.degree),
      major: asString(item.major),
      start: asString(item.start),
      end: asString(item.end),
    };
  });

  const custom_sections = (Array.isArray(source.custom_sections) ? source.custom_sections : []).map(
    (raw) => {
      const item = (raw ?? {}) as Record<string, unknown>;
      return {
        title: asString(item.title),
        items: asStringArray(item.items),
      };
    },
  );

  return {
    basics: {
      name: asString(basics.name),
      target_title: asString(basics.target_title),
      phone: asString(basics.phone),
      email: asString(basics.email),
      city: asString(basics.city),
      links: asStringArray(basics.links),
    },
    summary: asString(source.summary),
    work,
    projects,
    education,
    skills: asStringArray(source.skills),
    certificates: asStringArray(source.certificates),
    awards: asStringArray(source.awards),
    custom_sections,
  };
}

/**
 * 把 ParsedResume（§7.1）映射成 EditorContent（§2.5）。
 *
 * ParsedResume 里没有的字段（`summary` / `awards` / `custom_sections` /
 * `basics.target_title`）保持为空，由用户自行补充。
 */
export function editorContentFromParsed(parsed: ParsedResume | null | undefined): EditorContent {
  const base = normalizeEditorContent(undefined);
  if (!parsed) return base;

  const basics = (parsed.basics ?? {}) as Partial<ParsedResume['basics']>;

  return {
    basics: {
      name: asString(basics.name),
      target_title: '',
      phone: asString(basics.phone),
      email: asString(basics.email),
      city: asString(basics.city),
      links: asStringArray(basics.links),
    },
    summary: '',
    work: (parsed.work ?? []).map((item) => ({
      company: asString(item?.company),
      title: asString(item?.title),
      start: asString(item?.start),
      end: asString(item?.end),
      location: asString(item?.location),
      highlights: asStringArray(item?.highlights),
    })),
    projects: (parsed.projects ?? []).map((item) => ({
      name: asString(item?.name),
      role: asString(item?.role),
      start: asString(item?.start),
      end: asString(item?.end),
      tech: asStringArray(item?.tech),
      highlights: asStringArray(item?.highlights),
    })),
    education: (parsed.education ?? []).map((item) => ({
      school: asString(item?.school),
      degree: asString(item?.degree),
      major: asString(item?.major),
      start: asString(item?.start),
      end: asString(item?.end),
    })),
    skills: asStringArray(parsed.skills),
    certificates: asStringArray(parsed.certificates),
    awards: [],
    custom_sections: [],
  };
}

/** 某个区块是否已经填了内容（用于完成度统计与轻提示）。 */
export function isSectionFilled(content: EditorContent, key: TemplateSectionKey): boolean {
  switch (key) {
    case 'basics': {
      const { name, target_title, phone, email, city, links } = content.basics;
      return hasText(name, target_title, phone, email, city) || links.some((l) => l.trim() !== '');
    }
    case 'summary':
      return content.summary.trim().length > 0;
    case 'work':
      return content.work.some((item) => hasText(item.company, item.title) || item.highlights.length > 0);
    case 'projects':
      return content.projects.some((item) => hasText(item.name, item.role) || item.highlights.length > 0);
    case 'education':
      return content.education.some((item) => hasText(item.school, item.degree, item.major));
    case 'skills':
      return content.skills.some((item) => item.trim() !== '');
    case 'certificates':
      return content.certificates.some((item) => item.trim() !== '');
    case 'awards':
      return content.awards.some((item) => item.trim() !== '');
    case 'custom':
      return content.custom_sections.some(
        (section) => section.title.trim() !== '' || section.items.some((i) => i.trim() !== ''),
      );
    default:
      return false;
  }
}

/** 已填区块数（例如 6/9）。 */
export function countFilledSections(content: EditorContent): number {
  return EDITOR_SECTION_KEYS.filter((key) => isSectionFilled(content, key)).length;
}

/** 仍然为空的关键区块文案列表。 */
export function emptyKeySections(content: EditorContent): string[] {
  return KEY_SECTION_KEYS.filter((key) => !isSectionFilled(content, key)).map(
    (key) => SECTION_TITLES[key],
  );
}

function joinRange(start: string, end: string): string {
  const from = start.trim();
  const to = end.trim();
  if (from && to) return `${from} - ${to}`;
  return from || to;
}

function bullet(lines: string[], text: string, marker = '-'): void {
  const value = text.trim();
  if (value) lines.push(`${marker} ${value}`);
}

function paragraph(blocks: string[], lines: string[]): void {
  const body = lines.join('\n').trim();
  if (body) blocks.push(body);
}

/**
 * 把当前内容渲染成 Markdown（顺序与后端一致），用于「复制 Markdown」。
 * 空区块不渲染。
 */
export function buildResumeMarkdown(content: EditorContent): string {
  const blocks: string[] = [];
  const basics = content.basics;

  const header: string[] = [];
  if (basics.name.trim()) header.push(`# ${basics.name.trim()}`);
  if (basics.target_title.trim()) header.push(`**${basics.target_title.trim()}**`);
  const contact = [basics.phone, basics.email, basics.city, ...basics.links]
    .map((value) => value.trim())
    .filter(Boolean)
    .join(' | ');
  if (contact) header.push(contact);
  paragraph(blocks, header);

  if (content.summary.trim()) {
    blocks.push(`## ${SECTION_TITLES.summary}\n${content.summary.trim()}`);
  }

  if (content.work.length > 0) {
    const lines: string[] = [`## ${SECTION_TITLES.work}`];
    content.work.forEach((item) => {
      const title = [item.company, item.title].map((v) => v.trim()).filter(Boolean).join(' | ');
      if (title) lines.push('', `### ${title}`);
      const meta = [joinRange(item.start, item.end), item.location.trim()]
        .filter(Boolean)
        .join(' · ');
      if (meta) lines.push(meta);
      item.highlights.forEach((highlight) => bullet(lines, highlight));
    });
    paragraph(blocks, lines);
  }

  if (content.projects.length > 0) {
    const lines: string[] = [`## ${SECTION_TITLES.projects}`];
    content.projects.forEach((item) => {
      const title = [item.name, item.role].map((v) => v.trim()).filter(Boolean).join(' | ');
      if (title) lines.push('', `### ${title}`);
      const tech = item.tech.map((t) => t.trim()).filter(Boolean).join('、');
      const meta = [joinRange(item.start, item.end), tech].filter(Boolean).join(' · ');
      if (meta) lines.push(meta);
      item.highlights.forEach((highlight) => bullet(lines, highlight));
    });
    paragraph(blocks, lines);
  }

  if (content.education.length > 0) {
    const lines: string[] = [`## ${SECTION_TITLES.education}`];
    content.education.forEach((item) => {
      const title = [item.school, item.degree, item.major]
        .map((v) => v.trim())
        .filter(Boolean)
        .join(' | ');
      if (title) lines.push('', `### ${title}`);
      const meta = joinRange(item.start, item.end);
      if (meta) lines.push(meta);
    });
    paragraph(blocks, lines);
  }

  const plainLists: Array<[string, string[]]> = [
    [SECTION_TITLES.skills, content.skills],
    [SECTION_TITLES.certificates, content.certificates],
    [SECTION_TITLES.awards, content.awards],
  ];
  plainLists.forEach(([title, items]) => {
    const values = items.map((item) => item.trim()).filter(Boolean);
    if (values.length === 0) return;
    const lines: string[] = [`## ${title}`];
    values.forEach((value) => bullet(lines, value));
    paragraph(blocks, lines);
  });

  content.custom_sections.forEach((section) => {
    const title = section.title.trim();
    const values = section.items.map((item) => item.trim()).filter(Boolean);
    if (!title && values.length === 0) return;
    const lines: string[] = [`## ${title || SECTION_TITLES.custom}`];
    values.forEach((value) => bullet(lines, value));
    paragraph(blocks, lines);
  });

  return blocks.join('\n\n');
}

/** 默认简历标题（用户没填时用）。 */
export function defaultResumeTitle(content: EditorContent): string {
  const name = content.basics.name.trim();
  const target = content.basics.target_title.trim();
  if (name && target) return `${name}-${target}`;
  if (name) return `${name}-简历`;
  return '我的简历';
}
