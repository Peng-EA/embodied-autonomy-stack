# -*- coding: utf-8 -*-
"""生成 docs/assets/architecture-long.png（四层神经系统长图，1800x1240）。

用法（不要依赖任何第三方渲染器）：
    python docs/assets/make_figure.py

为什么用脚本画而不是拖 drawio / 用 mermaid-cli：
- 不引入 ~150MB 的 headless 浏览器依赖；
- 图里的文字与语义源 architecture-long.mmd 保持一致，改动可 diff；
- 中文字体直接取系统字体，避免乱码。

改图请同时更新 architecture-long.mmd（语义源）与本文件（渲染源）。
"""
import io
import os
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "architecture-long.png")
BOLD = r"C:\Windows\Fonts\msyhbd.ttc"
REG = r"C:\Windows\Fonts\msyh.ttc"

for _p in (BOLD, REG):
    if not os.path.exists(_p):
        raise SystemExit("缺少中文字体: %s（请改本文件顶部的字体路径）" % _p)

W, H = 1800, 1240
img = Image.new("RGB", (W, H), "#FFFFFF")
d = ImageDraw.Draw(img)

F_TITLE = ImageFont.truetype(BOLD, 40)
F_SUB = ImageFont.truetype(REG, 21)
F_LV = ImageFont.truetype(BOLD, 34)
F_H = ImageFont.truetype(BOLD, 25)
F_B = ImageFont.truetype(REG, 20)
F_S = ImageFont.truetype(REG, 17)
F_NOTE = ImageFont.truetype(REG, 19)

INK, MUTED, LINE = "#1F2933", "#5C6B7A", "#8C9AA8"

d.text((110, 48), "Embodied Autonomy Stack · 四层神经系统", font=F_TITLE, fill=INK)
d.text((110, 108), "LLM 是 L4 调用的外部异步服务，不是实时主循环；任何一层都可被更高优先级抢占。", font=F_SUB, fill=MUTED)
d.line([(110, 156), (W - 110, 156)], fill="#D9E0E7", width=2)

LAYERS = [
    dict(tag="L4", name="规划协调层", ts="1–10 s+", color="#F1EAFB", border="#8E6BC0",
         body=["接收目标候选，协调任务分解，调用 LLM，下发执行计划",
               "LLM 参与：主场（调用异步 LLM 服务，可断线降级）"]),
    dict(tag="L3", name="情感 / 动机层", ts="0.1–1 s", color="#FFF4E5", border="#D99A45",
         body=["连续情绪向量（效价 / 唤醒 / 支配…），调制 L2 参数",
               "生成目标候选与优先级；只调制，不越权改安全阈值"]),
    dict(tag="L2", name="反射层", ts="10–100 ms", color="#E9F7EE", border="#4E9C6B",
         body=["避障、平衡、力控、接触修正；毫秒级本地控制",
               "硬否决权：任何状态下触碰阈值一律截断"]),
    dict(tag="L1", name="内感受 / 稳态层", ts="10–100 ms", color="#E9F1FC", border="#4A79B8",
         body=["电量、温度、关节应力、任务进度；设定点偏离检测",
               "全局广播，拥有最高抢占权"]),
]

TOP, BH, GAP = 200, 150, 58
BX0, BX1 = 110, 1110

BOX = {}
for i, L in enumerate(LAYERS):
    y0 = TOP + i * (BH + GAP)
    BOX[L["tag"]] = (y0, y0 + BH)
    d.rounded_rectangle([BX0, y0, BX1, y0 + BH], radius=14, fill=L["color"], outline=L["border"], width=3)
    d.rounded_rectangle([BX0, y0, BX0 + 126, y0 + BH], radius=14, fill=L["border"])
    d.rectangle([BX0 + 96, y0, BX0 + 126, y0 + BH], fill=L["border"])
    tw = d.textlength(L["tag"], font=F_LV)
    d.text((BX0 + 63 - tw / 2, y0 + BH / 2 - 28), L["tag"], font=F_LV, fill="#FFFFFF")
    d.text((BX0 + 150, y0 + 18), f'{L["name"]}   ·   {L["ts"]}', font=F_H, fill=INK)
    for k, line in enumerate(L["body"]):
        d.text((BX0 + 150, y0 + 64 + k * 32), line, font=F_B, fill=MUTED)


def vline_arrow(x, y0, y1, color=LINE, w=3):
    """竖直箭头：y0 → y1（自动判断方向）。"""
    d.line([(x, y0), (x, y1)], fill=color, width=w)
    s = 11
    if y1 > y0:
        d.polygon([(x - s, y1 - s - 2), (x + s, y1 - s - 2), (x, y1 - 1)], fill=color)
    else:
        d.polygon([(x - s, y1 + s + 2), (x + s, y1 + s + 2), (x, y1 + 1)], fill=color)


def vlabel(x, yc, text, font=F_S, fill=MUTED):
    """竖排（逆序阅读，自下而上）标签，居中于 yc。"""
    tw = int(d.textlength(text, font=font))
    th = 24
    tmp = Image.new("RGBA", (tw + 8, th + 8), (255, 255, 255, 0))
    td = ImageDraw.Draw(tmp)
    td.text((4, 2), text, font=font, fill=fill)
    rot = tmp.rotate(90, expand=True)
    img.paste(rot, (int(x), int(yc - rot.height / 2)), rot)


# ---- 相邻层之间的短箭头（画在层间空隙里）
GX1, GX2 = 980, 1050

def gap_arrow(x, tag_a, tag_b, up=True, label=None, side="left"):
    y_low = BOX[tag_a][0] - 6 if up else BOX[tag_a][1] + 6
    if up:  # 从下层顶部向上
        y0, y1 = BOX[tag_b][0] + 6, BOX[tag_a][1] - 6
        d.line([(x, y0), (x, y1)], fill=LINE, width=3)
        s = 10
        d.polygon([(x - s, y1 + s + 2), (x + s, y1 + s + 2), (x, y1 + 2)], fill=LINE)
        if label:
            d.text((x - 16 - d.textlength(label, font=F_S), (y0 + y1) / 2 - 11), label, font=F_S, fill=MUTED)
    else:
        y0, y1 = BOX[tag_a][1] + 6, BOX[tag_b][0] - 6
        d.line([(x, y0), (x, y1)], fill=LINE, width=3)
        s = 10
        d.polygon([(x - s, y1 - s - 2), (x + s, y1 - s - 2), (x, y1 - 2)], fill=LINE)
        if label:
            d.text((x + 16, (y0 + y1) / 2 - 11), label, font=F_S, fill=MUTED)


# L3 → L4 : goal_candidate（上）
gap_arrow(GX1, "L4", "L3", up=True, label="goal_candidate")
# L2 → L3 : outcome_event（上）
gap_arrow(GX1, "L3", "L2", up=True, label="outcome_event")
# L2 → L1 : outcome_event（上）
gap_arrow(GX1, "L2", "L1", up=True, label="outcome_event")
# L3 → L2 : affect_vector（下，调制参数）
gap_arrow(GX2, "L3", "L2", up=False, label="affect_vector")

# ---- 跨层竖排长箭头（右侧导轨）
RX1, RX2 = 1222, 1274
vline_arrow(RX1, BOX["L1"][0] - 2, BOX["L4"][1] + 4)
vlabel(RX1 - 22, (BOX["L1"][0] + BOX["L4"][1]) / 2, "homeostatic_broadcast（可抢占全局）")
vline_arrow(RX2, BOX["L2"][0] - 2, BOX["L4"][1] + 4)
vlabel(RX2 + 10, (BOX["L2"][0] + BOX["L4"][1]) / 2, "reflex_override（硬中断）")

# ---- 外部 LLM
LX0, LX1 = 1440, 1772
ly0, ly1 = BOX["L4"][0] + 6, BOX["L4"][1] - 6


def dashed_rect(box, color, width=3, dash=12, gap=8):
    x0, y0, x1, y1 = box
    for (xa, ya, xb, yb) in [(x0, y0, x1, y0), (x1, y0, x1, y1), (x1, y1, x0, y1), (x0, y1, x0, y0)]:
        length = max(abs(xb - xa), abs(yb - ya))
        n = int(length // (dash + gap)) + 1
        for k in range(n):
            t0 = k * (dash + gap) / length
            t1 = min((k * (dash + gap) + dash) / length, 1.0)
            d.line([(xa + (xb - xa) * t0, ya + (yb - ya) * t0),
                    (xa + (xb - xa) * t1, ya + (yb - ya) * t1)], fill=color, width=width)


dashed_rect([LX0, ly0, LX1 - 30, ly1], "#9AA7B4")
d.text((LX0 + 24, ly0 + 16), "LLM Semantic Service", font=F_H, fill=INK)
d.text((LX0 + 24, ly0 + 56), "外部服务 · 异步 · 可插拔", font=F_B, fill=MUTED)
d.text((LX0 + 24, ly0 + 86), "断线 → L4 降级为本地策略", font=F_B, fill=MUTED)

y1, y2 = ly0 + 46, ly1 - 34
d.line([(BX1 + 6, y1), (LX0 - 6, y1)], fill=LINE, width=3)
d.polygon([(LX0 - 18, y1 - 8), (LX0 - 18, y1 + 8), (LX0 - 4, y1)], fill=LINE)
d.text((BX1 + 14, y1 - 26), "semantic_plan_request", font=F_S, fill=MUTED)

d.line([(LX0 - 6, y2), (BX1 + 6, y2)], fill=LINE, width=3)
d.polygon([(BX1 + 18, y2 - 8), (BX1 + 18, y2 + 8), (BX1 + 4, y2)], fill=LINE)
d.text((BX1 + 14, y2 + 10), "semantic_plan_result", font=F_S, fill=MUTED)

# ---- 底部说明
NY = 1000
d.line([(110, NY), (W - 110, NY)], fill="#D9E0E7", width=2)
notes = [
    "契约原则：L1 广播最高抢占权 · L2 绝对否决权 · L3 只调制不越权 · L4 异步、可插拔、可断线",
    "四层神经系统是工程隐喻，不是生物等价声明；本项目不主张机器人具有生物神经系统或主观体验。",
    "情绪影响偏好，不影响安全：情绪向量只调制参数（探索率、避障距离、交互频率），且有饱和与衰减。",
    "详细定义：docs/architecture.md · 契约：docs/interfaces.md · 安全：docs/safety.md · 评价：docs/evaluation.md",
]
for k, t in enumerate(notes):
    d.ellipse([110, NY + 36 + k * 42, 119, NY + 45 + k * 42], fill="#8E6BC0")
    d.text((132, NY + 26 + k * 42), t, font=F_NOTE, fill=MUTED if k < 3 else "#4A79B8")

img.save(OUT, "PNG", optimize=True)
print("saved", OUT, os.path.getsize(OUT), img.size)
