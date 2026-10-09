import type { Config } from 'tailwindcss';

/**
 * 设计令牌 —— Claude 风格（暖色编辑感）
 *
 * ## 为什么"重映射"内置色板名，而不是逐个文件改类名
 *
 * 本次改造把 `slate` / `red` / `amber` / `green` 这几个 Tailwind 内置色板
 * **整体替换为暖色调版本**，而不是把全站 20 多个文件里的 `slate-*` 手动改名为 `ink-*`。
 * 理由：
 * 1. 配色温度是"全局属性"，一处配置改完，改动可审阅、回归风险低；
 * 2. 新代码即使顺手写 `slate-500`，落到的也是暖灰，不会意外引入冷色孤岛；
 * 3. 代价是"slate 不再是冷灰"这一反直觉之处 —— 所以在此显式声明，并额外提供
 *    语义化别名 `ink`（与 slate 同值），新代码优先用 `ink-*`。
 *
 * 已确认：全站只有 `badge.tsx` 的 `blue` 变体用到不可重映射的颜色族，且该变体从未被使用（已移除）。
 *
 * ## 调色板来源
 * Claude / Anthropic 产品界面的特征：米白底（#FAF9F5）、暖灰文字（#1A1915 而非纯黑）、
 * 克制的珊瑚/陶土强调色（#D97757），语义色也偏暖（砖红 / 赭黄 / 苔绿）而不是高饱和的原色。
 */

// 暖中性色：米白 → 暖灰 → 暖近黑（刻意不用纯黑 #000）
const ink = {
  50: '#FAF9F5',
  100: '#F4F2EA',
  200: '#E8E5DA',
  300: '#D3CFC0',
  400: '#7E7B6E',
  500: '#67655B',
  600: '#514F47',
  700: '#3B3A34',
  800: '#262520',
  900: '#171612',
  950: '#0E0E0B',
};

// Claude 签名色：珊瑚 / 陶土。
// 注意 500 用于文字与焦点环（签名观感），600 用于按钮填充（白字对比度更好）。
const brand = {
  50: '#FBF3EF',
  100: '#F7E6DE',
  200: '#EFCFC0',
  300: '#E4B29C',
  400: '#DC9275',
  500: '#D97757',
  600: '#C4603F',
  700: '#A44E31',
  800: '#833E27',
  900: '#6A3320',
};

// 语义色也统一去饱和、偏暖，避免高饱和原色在米白底上"喊叫"
const warmRed = {
  50: '#FCF1EE',
  100: '#F8DFD8',
  200: '#EFC0B2',
  300: '#E29B88',
  400: '#D2745A',
  500: '#BC4A2F',
  600: '#9E3A24',
  700: '#83301D',
  800: '#6A2717',
  900: '#571F13',
};

const warmAmber = {
  50: '#FBF6EA',
  100: '#F6EBCF',
  200: '#EBD79F',
  300: '#DDBC68',
  400: '#C99E3A',
  500: '#B4802F',
  600: '#96682A',
  700: '#7A5423',
  800: '#61421C',
  900: '#4E3517',
};

const warmGreen = {
  50: '#F1F6F2',
  100: '#DCEAE0',
  200: '#B8D5C0',
  300: '#8CB99A',
  400: '#5F9B72',
  500: '#3F7A5A',
  600: '#336449',
  700: '#2A523C',
  800: '#224130',
  900: '#1C3427',
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
        // 正文：系统无衬线（中文优先 PingFang / 雅黑），保证中文渲染稳定
        sans: [
          '-apple-system',
          'BlinkMacSystemFont',
          '"Segoe UI"',
          '"PingFang SC"',
          '"Hiragino Sans GB"',
          '"Microsoft YaHei"',
          '"Source Han Sans SC"',
          '"Noto Sans SC"',
          'sans-serif',
        ],
        // 标题：拉丁走 Georgia（自带编辑气质），中文回落到系统宋体族
        serif: [
          'Georgia',
          '"Source Serif 4"',
          '"Songti SC"',
          'SimSun',
          '"Source Han Serif SC"',
          '"Noto Serif SC"',
          '"Times New Roman"',
          'serif',
        ],
        mono: [
          '"SFMono-Regular"',
          'Consolas',
          '"Liberation Mono"',
          'Menlo',
          'monospace',
        ],
      },
      colors: {
        brand,
        ink,
        // ↓ 内置色板重映射：让全站既有类名自动落到暖色调上
        slate: ink,
        red: warmRed,
        amber: warmAmber,
        green: warmGreen,
      },
      borderRadius: {
        card: '14px',
      },
      boxShadow: {
        // 阴影带暖色调（而非纯黑低透明度），并保持极轻 —— Claude 用发丝边框而非重阴影表达层级
        sm: '0 1px 2px 0 rgba(23,22,18,0.04), 0 1px 1px -1px rgba(23,22,18,0.03)',
        card: '0 1px 2px 0 rgba(23,22,18,0.04), 0 1px 1px -1px rgba(23,22,18,0.03)',
        md: '0 10px 24px -8px rgba(23,22,18,0.13), 0 2px 6px -2px rgba(23,22,18,0.06)',
        pop: '0 16px 40px -12px rgba(23,22,18,0.18), 0 4px 10px -4px rgba(23,22,18,0.08)',
      },
      transitionTimingFunction: {
        spring: 'cubic-bezier(0.22, 1, 0.36, 1)',
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
