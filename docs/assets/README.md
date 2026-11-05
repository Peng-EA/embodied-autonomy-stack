# assets

| 文件 | 是什么 |
|---|---|
| `architecture-long.png` | 四层神经系统长图（README 与 docs/architecture.md 引用） |
| `architecture-long.mmd` | **语义源**：等价的 mermaid 定义，可在 GitHub 直接渲染，改图先改这里 |
| `make_figure.py` | **渲染源**：生成上面那张 PNG。不依赖 mermaid-cli / headless 浏览器 |

## 为什么两套源

mermaid 好看但需要渲染器；PNG 稳定但这张图里有中文、布局要控制。
所以：`.mmd` 保证语义可读、可在 GitHub 上渲染；`make_figure.py` 保证离线能出图、且没有
150MB 的 headless 浏览器依赖。

**改图时两边都要改**，否则图与语义会分叉——这类分叉是文档腐化的开始。

## 重新生成

```bash
python docs/assets/make_figure.py
# 需要 Pillow；中文字体路径写死在脚本顶部（Windows: msyhbd.ttc / msyh.ttc）
```

## 待补

- [ ] `architecture-realtime.png`：L1–L2 实时层拆分（P1）
- [ ] `architecture-cognitive.png`：L3–L4 认知层拆分（P1）
