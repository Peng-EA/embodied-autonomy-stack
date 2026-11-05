# 文档站（VitePress）

> 站点源码在 `site/`，内容页面（architecture / interfaces / evaluation …）在**第二步**接入。
> 目前 `site/index.md` 是唯一手写页面：Hero + 架构图 + 基线矩阵 + 入口。

## 为什么站点独立于 `docs/`

`docs/` 是**唯一事实源**（GitHub 上直接读的正文）。站点不复制一份正文来维护，
否则两份必然分裂——这正是本仓库 `docs/README.md` 里写明的反模式。

第二步会加一个同步脚本：`docs/*.md` → `site/content/*.md`，
在构建时把 `../contracts/...` 这类仓库内相对链接改写成站点路由或 GitHub 绝对链接。
**在此之前，站点导航直接指向 GitHub 原文**，不造空页面。

## 本地开发

```bash
cd site
pnpm install
pnpm docs:dev        # http://localhost:5173
pnpm docs:build      # 产物在 site/.vitepress/dist
pnpm docs:preview
```

## 部署（Vercel）

1. Vercel → New Project → 导入 `Peng-EA/embodied-autonomy-stack`。
2. **Root Directory 选 `site`**（其余由 `site/vercel.json` 接管）。
3. Framework Preset: VitePress；Build: `pnpm run docs:build`；Output: `.vitepress/dist`。
4. 绑定域名：Project → Settings → Domains → 添加你的域，按提示加 CNAME。
5. 绑定后把 `site/.vitepress/config.mts` 里的 `sitemap.hostname` 改成真实域名。

## 设计约定

| 项 | 约定 |
|---|---|
| 主题 | 深色优先（`appearance: 'dark'`），可切换 |
| 主色 | 终端绿 `#35D07F`；警示用内感受红 `#FF4D5E`；时间尺度用琥珀 `#FFB454` |
| 字体 | 等宽（JetBrains Mono，CJK 回落到系统字体） |
| 数字 | 一律 `font-variant-numeric: tabular-nums`，保证纵向对齐 |
| 动效 | 只有光标闪烁与 1px 位移；`prefers-reduced-motion` 下全部关闭 |
| 空态 | 不允许出现空页面/占位页；没内容就指向 GitHub |

## Mermaid

用自建 `<Mermaid>` 组件（`theme/components/Mermaid.vue`）而不是插件：

- 插件对 mermaid 11 + SSR 的兼容性需要额外压测，这里不赌；
- 自建可以完全控制深色主题变量（颜色/字体/间距）与失败兜底（渲染失败时回退显示源码）；
- 代价：图在客户端渲染，首屏会有一帧空白，已用 CSS 兜住。
