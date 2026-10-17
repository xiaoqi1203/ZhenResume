import type { Config } from 'tailwindcss';

/**
 * 设计令牌 —— 「紫纸 / 黑白高对比 / 直角 / 发丝线」风格
 *
 * ## 风格来源
 * 参照 `image_demo/img_1.png`（skillset 风格）：
 * - **电光紫画布**（#7C00FF 系）包住一张白色纸面，紫只做画布与强调，不做渐变；
 * - **纯黑白高对比**：文字近纯黑、按钮黑底白字、卡片白底细线；
 * - **全直角**：所有圆角归零，靠 1px 发丝线与留白划分层级，不用阴影；
 * - **无衬线重标题**：大字号 + 紧字距 + 800 字重，标题与正文同族；
 * - **等宽数字**：评分、分数差、成本等一律 tabular-nums。
 *
 * ## 为什么仍然"重映射内置色板"
 * 与上一版同样的理由：`slate` / `red` / `amber` / `green` 被全站 20+ 文件使用，
 * 一处重映射即可整体换色，改动可审阅、回归风险低；新代码优先用语义别名 `ink` / `brand`。
 *
 * ## 与上一版（Claude 暖色）的关键差异
 * | 维度 | 旧 | 新 |
 * |---|---|---|
 * | 主色 | 陶土珊瑚 #D97757 | **电光紫 #7C00FF** |
 * | 中性色 | 暖灰（偏黄） | **中性灰（无色相）** |
 * | 圆角 | 14px 卡片 + 10px 控件 | **全部 0（直角）** |
 * | 阴影 | 轻暖阴影表达层级 | **无阴影，1px 边框表达层级** |
 * | 标题字体 | 衬线（编辑感） | **无衬线 + 800 字重（Swiss 感）** |
 */

// 中性灰：无彩色相，从纸白到近黑（刻意不用纯黑做正文，用 #18181B 保证屏幕可读）
const ink = {
  50: '#FAFAFA',
  100: '#F4F4F5',
  200: '#E7E7E9',
  300: '#D4D4D8',
  400: '#A1A1AA',
  500: '#71717A',
  600: '#52525B',
  700: '#3F3F46',
  800: '#27272A',
  900: '#18181B',
  950: '#09090B',
};

// 电光紫：参考图的主色。500 是画布/强调，600 用于可点击文字（对比度更好）
const brand = {
  50: '#F6EDFF',
  100: '#EDDBFF',
  200: '#DBB8FF',
  300: '#C188FF',
  400: '#A155FF',
  500: '#7C00FF',
  600: '#6A00DB',
  700: '#5700B4',
  800: '#44008C',
  900: '#33006A',
};

// 语义色：保持标准语义色（红/黄/绿），但用法上以**细边框 + 文字色**为主，不用大面积填充
const red = {
  50: '#FEF2F2',
  100: '#FEE2E2',
  200: '#FECACA',
  300: '#FCA5A5',
  400: '#F87171',
  500: '#EF4444',
  600: '#DC2626',
  700: '#B91C1C',
  800: '#991B1B',
  900: '#7F1D1D',
};

const amber = {
  50: '#FFFBEB',
  100: '#FEF3C7',
  200: '#FDE68A',
  300: '#FCD34D',
  400: '#FBBF24',
  500: '#F59E0B',
  600: '#D97706',
  700: '#B45309',
  800: '#92400E',
  900: '#78350F',
};

const green = {
  50: '#F0FDF4',
  100: '#DCFCE7',
  200: '#BBF7D0',
  300: '#86EFAC',
  400: '#4ADE80',
  500: '#22C55E',
  600: '#16A34A',
  700: '#15803D',
  800: '#166534',
  900: '#14532D',
};

const config: Config = {
  content: [
    './app/**/*.{ts,tsx}',
    './components/**/*.{ts,tsx}',
    './lib/**/*.{ts,tsx}',
  ],
  theme: {
    extend: {
      fontFamily: {
        // 标题与正文同族：无衬线（中文优先 PingFang / 雅黑），靠字重与字距建立层级
        sans: [
          '"Helvetica Neue"',
          'Helvetica',
          '"Segoe UI"',
          '"PingFang SC"',
          '"Hiragino Sans GB"',
          '"Microsoft YaHei"',
          '"Source Han Sans SC"',
          '"Noto Sans SC"',
          'Arial',
          'sans-serif',
        ],
        // 旧代码里仍有 `font-serif` 的地方一律回落到无衬线（风格统一，不留衬线孤岛）
        serif: [
          '"Helvetica Neue"',
          'Helvetica',
          '"PingFang SC"',
          '"Microsoft YaHei"',
          'Arial',
          'sans-serif',
        ],
        mono: [
          '"SFMono-Regular"',
          '"JetBrains Mono"',
          'Consolas',
          '"Liberation Mono"',
          'Menlo',
          'monospace',
        ],
      },
      colors: {
        brand,
        ink,
        // ↓ 内置色板重映射：既有类名自动落到新调色板
        slate: ink,
        red,
        amber,
        green,
      },
      borderRadius: {
        // 全直角：把常用档位显式归零，避免既有 `rounded-lg` 等类名残留圆角
        none: '0px',
        sm: '0px',
        DEFAULT: '0px',
        md: '0px',
        lg: '0px',
        xl: '0px',
        '2xl': '0px',
        '3xl': '0px',
        card: '0px',
        full: '9999px', // 仅头像/圆点/圆形箭头按钮使用
      },
      boxShadow: {
        // 层级由 1px 边框与留白承担；阴影全部归零（保留 key 以免旧类名报错）
        sm: 'none',
        DEFAULT: 'none',
        md: 'none',
        lg: 'none',
        pop: 'none',
        card: 'none',
        // 唯一保留的"浮起"手段：4px 实心黑色错位阴影（重点卡片/选中态）
        hard: '4px 4px 0 0 #18181B',
        'hard-brand': '4px 4px 0 0 #7C00FF',
      },
      letterSpacing: {
        tightest: '-0.045em',
        tighter: '-0.03em',
      },
      transitionTimingFunction: {
        spring: 'cubic-bezier(0.22, 1, 0.36, 1)',
        snap: 'cubic-bezier(0.2, 0, 0, 1)',
      },
      keyframes: {
        'fade-in': {
          from: { opacity: '0', transform: 'translateY(4px)' },
          to: { opacity: '1', transform: 'translateY(0)' },
        },
        rise: {
          from: { opacity: '0', transform: 'translateY(10px)' },
          to: { opacity: '1', transform: 'translateY(0)' },
        },
      },
      animation: {
        'fade-in': 'fade-in 0.2s ease-out',
        rise: 'rise 0.5s cubic-bezier(0.22, 1, 0.36, 1) both',
      },
    },
  },
  plugins: [],
};

export default config;
