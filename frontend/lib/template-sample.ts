/**
 * 模板画廊用的示例内容（一份「填好的」中文产品经理简历）。
 *
 * 为什么单独抽一份常量，而不是复用后端某个模板的 `example_content`：
 * 1. 画廊卡片缩略图在打开列表接口时就要渲染，而列表接口刻意不返回
 *    `example_content`（响应太大）；等详情接口回来再画缩略图会闪一屏空白。
 * 2. 4 套版式共用同一份文案，卡片之间只有版式差异，用户比较容易横向比较。
 * 3. 内容是**只为展示**的静态样例，不参与诊断、不会被写进用户简历草稿。
 *
 * 篇幅按一页 A4 控制（工作 2 段、项目 2 个、每段 2–3 条量化要点），
 * 保证缩略图里区块完整、不出现大段留白；不带头像（`avatar` 不写 = 不渲染）。
 */

import type { EditorContent } from '@/lib/types';

export const TEMPLATE_PREVIEW_CONTENT: EditorContent = {
  basics: {
    name: '林安',
    target_title: '产品经理',
    phone: '138-0013-8000',
    email: 'lin.an@example.com',
    city: '上海',
    // 作品集链接会和手机 / 邮箱 / 城市拼成一行联系方式，缩略图里可见
    links: ['作品集 linan.design'],
  },
  summary:
    '3 年 B 端 SaaS 产品经验，主导客户成功中台从 0 到 1 落地；擅长把模糊的业务诉求拆成可验证的指标与迭代路径，用数据驱动续约与转化。',
  work: [
    {
      company: '云舟科技',
      title: '高级产品经理',
      start: '2022.06',
      end: '至今',
      location: '上海',
      highlights: [
        '主导客户成功中台从 0 到 1 落地，覆盖 1200+ 企业客户，年度续约率从 78% 提升到 91%。',
        '重构工单与 SLA 流程，平均首次响应时长由 4.2 小时缩短到 1.5 小时，客诉量下降 27%。',
        '搭建产品数据看板，把周报取数从每周 3 小时人工整理压缩到 10 分钟自动生成。',
      ],
    },
    {
      company: '星野网络',
      title: '产品经理',
      start: '2020.07',
      end: '2022.05',
      location: '上海',
      highlights: [
        '负责会员成长体系设计，上线 6 个月付费转化率提升 18%，ARPU 提升 12%。',
        '推动 A/B 实验平台接入，季度完成 24 组实验，需求验证周期缩短 40%。',
      ],
    },
  ],
  projects: [
    {
      name: '客户健康度评分模型',
      role: '产品负责人',
      start: '2023.03',
      end: '2023.11',
      tech: ['SQL', 'Python', 'Tableau'],
      highlights: [
        '定义 12 项健康度指标与权重，联合数据团队搭建自动评分与预警链路。',
        '上线后高风险客户提前 30 天预警，季度流失率下降 6.5 个百分点。',
      ],
    },
    {
      name: '企业自助入驻流程',
      role: '产品经理',
      start: '2022.02',
      end: '2022.09',
      tech: ['Figma', 'Axure'],
      highlights: [
        '把 7 步人工审核流程压缩为 3 步线上自助，平均入驻周期从 5 天缩短到 1 天。',
        '与销售、法务共建准入规则，人工审核量下降 62%。',
      ],
    },
  ],
  education: [
    {
      school: '华东师范大学',
      degree: '本科',
      major: '信息管理与信息系统',
      start: '2016.09',
      end: '2020.06',
    },
  ],
  skills: [
    '产品规划与需求拆解',
    'Axure / Figma',
    'SQL / Python 数据分析',
    'A/B 实验与指标设计',
    'B 端 PRD 与流程设计',
  ],
  certificates: ['PMP 项目管理专业人士认证', '大学英语六级（CET-6）'],
  awards: ['2023 年度云舟科技最佳产品奖', '全国大学生创新创业大赛二等奖'],
  custom_sections: [
    {
      title: '行业洞察',
      items: ['持续跟进 SaaS 客户成功与 PLG 增长案例，输出 20+ 篇内部拆解与方法论文档。'],
    },
  ],
};

export default TEMPLATE_PREVIEW_CONTENT;