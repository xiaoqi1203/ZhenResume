/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  eslint: {
    // 构建阶段不做 ESLint 阻断，lint 通过 `pnpm lint` 单独执行
    ignoreDuringBuilds: true,
  },
  // Docker 部署用：产出 .next/standalone 自包含产物（含最小化 node_modules + server.js），
  // 运行层镜像不必再装依赖。本地 `pnpm dev` / `pnpm build` / `pnpm start` 行为不变。
  // 详见 frontend/Dockerfile 与 docs/docker-deploy.md。
  output: 'standalone',
};

export default nextConfig;
