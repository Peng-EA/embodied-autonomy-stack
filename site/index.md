---
pageClass: home
sidebar: false
aside: false
title: Embodied Autonomy Stack
titleTemplate: false
---

<HomeHero />

## // 分层架构

L1 说"我饿了" → L3 说"那我现在想去找吃的" → L4 说"路径这样规划，话这么说" → L2 说"路上有坑我躲了"。

<ArchitectureDiagram />

点击任一层进入该层的接口契约。**LLM 不是 L4，它是 L4 调用的外部异步服务**——可插拔、可断线。

## // 对比基线

四条基线各自关掉一个机制，用来回答三个最容易被攻击的问题：比 LLM 中心决策好在哪、比传统行为树好在哪、情绪与抢占是不是装饰。

<BaselineMatrix />

<div class="ea-note">
  <strong>表中是预期方向，不是实测值。</strong>
  本项目 v0.1 只跑过单障碍物、10 秒、参数刚标定的仿真，任何把预期写成实测的文档都算数据造假。
  指标的分子、分母与通过线定义见
  <a href="/evaluation">docs/evaluation.md</a>。
</div>

## // 起点

<div class="ea-links">
  <a class="ea-link" href="/vision" target="_blank" rel="noreferrer">
    <small>01 / Vision</small><span>为什么 LLM 不能做实时中心</span>
  </a>
  <a class="ea-link" href="/interfaces" target="_blank" rel="noreferrer">
    <small>02 / Interfaces</small><span>七个层间信号与契约原则</span>
  </a>
  <a class="ea-link" href="https://github.com/Peng-EA/embodied-autonomy-stack/tree/main/contracts" target="_blank" rel="noreferrer">
    <small>03 / Contracts</small><span>JSON Schema：机器可校验的定义</span>
  </a>
  <a class="ea-link" href="/safety" target="_blank" rel="noreferrer">
    <small>04 / Safety</small><span>情绪影响偏好，不影响安全</span>
  </a>
  <a class="ea-link" href="/blog/why-llm-should-not-be-the-real-time-center">
    <small>05 / Blog</small><span>为什么 LLM 不该做实时中心（假说）</span>
  </a>
</div>

<div class="ea-footnote">
  四层神经系统是<strong>工程隐喻</strong>，不是生物等价声明。本项目中的"情绪"指可观测的内部状态调制变量，不主张主观体验。<br />
  不做通用人形整机 · 不做 LLM Agent 套壳 · 不承诺 AGI · 不把情感做成聊天标签 · 不跳过物理闭环谈自主。
</div>
