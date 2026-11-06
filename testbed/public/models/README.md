# 本地角色资产（不进仓库）

## 为什么这里大多是空的

| 模型 | 许可 | 是否随仓库分发 |
|---|---|---|
| `whale-child.glb`（小鲸鲸 · 幼年） | CC BY 4.0 | ✅ 是，署名见 `ATTRIBUTION.md` |
| `silverwolf/`（星穹铁道 · 银狼） | **请勿二次配布** / 禁止商用 | ❌ 否，只在本机 |

银狼这套 PMX 的 `使用规则.txt` 明确写了「请勿二次配布」，所以它被 `.gitignore` 排除。
克隆仓库的人看到的是兜底角色（小鲸鲸），不是银狼。

## 想在本地看到银狼

1. 把 PMX 及其贴图放进 `testbed/public/models/silverwolf/`；
2. 主文件命名为 `silverwolf.pmx`（贴图保持原名，PMX 内部按原名引用）；
3. `npm run dev` → 打开 `/console.html`。

控制台会按这个顺序找模型：

```
models/silverwolf/silverwolf.pmx   ← 银狼（本地，MMDLoader 直接读）
models/whale-child.glb             ← 兜底（仓库内，CC BY）
程序化占位                          ← 都没有时
```

## 换别的模型

- **PMX（MMD）**：放进同名目录，改 `src/console/main.ts` 里 `MMDLoader().load(...)` 的路径即可，
  蒙皮与材质由 `three/addons` 的 MMDLoader 处理（本仓库 vendored 在 `src/vendor/`，因为 three 0.187 已移除它）。
- **glTF/GLB**：改 `gltfLoader.load(...)` 的路径，模型需要自带骨骼才有点头/挥手这类动作。
