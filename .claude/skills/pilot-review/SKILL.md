---
name: pilot-review
description: Audit mesh-to-cad pilot exp dir(s), emit review.md + iteration
  playbook mapping issues to specific SKILL.md sections.
  Trigger: "pilot-review", "审阅 pilot", "看 outputs", "iterate CAD skill",
  "cvm 运行结果", "分析 pilot".
---

# pilot-review — mesh-to-cad exp dir audit

## 目的

审阅一个或多个 `outputs/<ts>-<obj>/` exp dir，检出 pilot 病症 + 产 "改哪段
SKILL.md" 的具体建议，让 CAD skill 的 iteration loop 从主观感受收敛到 objective
判据。

## Workflow

1. **判断输入**：`$ARGUMENTS` = exp dir 路径 / group 目录 / glob。
   - 布局约定：`outputs/<group>/<exp>/`（group = `YYYYMMDD-HHMMSS-<slug>`）
   - 给 group 目录（含 `_snapshot/`）→ 展开到该 group 下所有 `<exp>/`（跳过 `_snapshot/`）
   - 给单 exp dir → 只审那一个
   - 空 → `ls -td outputs/*/*/ | head -1` 取最新一个 exp
2. **加载权威 schema**（每次 Read，不 cache）：
   - `skills/mesh-to-cad/references/output-schemas.md`
   - `skills/mesh-compare/references/compare-metrics.md`
   - `skills/mesh-to-cad/references/routing-rubric.md`
3. **对每个 exp dir 执行 9 类检查**（每 check 独立，失败不阻断其他）：
   1. **notes.md 7-section schema**：Read `<exp>/notes.md`，正则匹配 7 个 `##` heading
      顺序 == `output-schemas.md § notes.md seven sections`。
   2. **route.json completeness**：Read `<exp>/route.json`，`considered_alternative`
      key 存在且 `.rejected_because` 非空字符串。
   3. **文件命名**：`ls <exp>/` 扫描是否有连字符（应全用 `_`）；有 → error。
   4. **Loop hygiene**：`cd <exp> && git log --oneline`；尾部 commit 的
      `verdict=` 必须是 `accept` 或 `plateau`（refine 结尾 = 跳步）。
   5. **Metric monotonicity**：如 git 里多 iter，对每 iter
      `git show HEAD~<i>:compare_metrics.json` 确认 `chamfer_l2` 单调非增；若
      不是，flag 为 divergence 却没被声明。
   6. **iou/chamfer sanity**：Read `<exp>/compare_metrics.json`；若
      `chamfer_l2 < 0.03 AND iou < 0.5` → metric 实现可疑（**不是** pilot 病，
      而是 `packages/meshscope` bug）。
   7. **Preservation heuristic**（severity=warn，允许假阳）：Read mesh_stats
      face_count + notes.md `## Preserved Structural Features` 段；若 face
      > 5万且 preserved 段没显式提到复数结构（buttons/wings/wheels/legs）→ warn。
   8. **Cost**：Read `<exp>/usage.json`（若存在）；baseline **USD 0.30 per pilot**
      （handoff 2026-07-23 明确；用 "USD 0.30" 避开 `$0` args 替换）；> 2× warn，
      > 5× error；同时报 `cache_hit_rate` if present。
   9. **Route replay**：Read mesh_stats.json；按 `routing-rubric.md` 5-priority
      手动重放决策，对比 `route.json.route`；不符 → warn。
4. **产报告**（见 § Handoff）。

## Non-negotiable

- **权威 schema 只 Read 不复制**：本 skill 里绝不硬编码 "notes.md 有 7 段" 这种
  规则；产品 skill 改了 schema 本 skill 引用同步生效。
- **每个 issue 必须带 fix target**：具体到 "改 `<file>` 的 `§ <section>`" 或
  "改 `<file>::<function>`"，不接受 "notes 有问题" 这种含糊建议。
- **review.md 落在 exp dir 内部**（`<exp>/review.md`），不落 review 集中目录。
  就近原则；exp 本身的 `.git/` 已有 trajectory，review 也在同处形成完整快照。
- **只审、不改**：本 skill 从不修 exp dir 里任何文件，只产报告。修 CAD skill
  是 dev + 后续 plan 的事。
- **Preservation heuristic 只出 warn，不出 error**：这类 heuristic 允许假阳，
  不能因它阻断 iteration。

## 边界条件

- 只识别 mesh-to-cad workflow 产出的 exp dir 结构（`route.json` +
  `compare_metrics.json` + `notes.md` + `.git/` + `previews/`）。**若未来加
  URDF/SDF 类 pilot** → gracefully skip（找不到 mesh-to-cad 产物 → warn + 跳过），
  不 crash。
- 不依赖 CAD skill 的运行时代码，只 Read 它们的 references 里的 markdown。
- 不发起网络请求，不调 model，不装依赖。纯本地 Read + jq/grep 逻辑。
- 单 exp 走 9 check；批量走 N × 9 check + summary 跨 exp。**没有 sampling**：
  10 个 exp 就跑 90 check，慢但完整。
- Iteration playbook 表格是 **empirical accumulate**，不是完整枚举。空 row ≠
  没问题，是"这类问题以前没撞过"。
- **`outputs/` 是 symlink 到 rclone mount**（2026-07-23 之后布局）：读文件走
  VFS cache，首次~200ms/文件，二次~10ms。一次 review 一个 exp 会预热该 exp
  的 cache。

## Handoff

对每个 exp dir 产：
- `<exp_dir>/review.md` — human-readable：按 severity (error/warn/info) 分组，
  每 issue 一行，含 fix target 引用
- `<exp_dir>/review.json` — machine-readable：issue 数组

批量模式（给 group 目录）额外产：
- `outputs/<group>/review-summary.md` — 跨 exp 汇总（"9 中 6 个 loop 跳步" =
  systemic drift），落 group 目录内部而非 outputs/ 根，与 `_snapshot/` 一起
  构成完整 batch 快照

stdout（Claude Code 里给用户）：
- 前 5 条 error inline
- 每个 exp 的 review.md 路径
- "下一步"建议：按最高频 issue 类型指哪段 SKILL.md 要改

Iteration playbook（**表格是活的**，每次新型 issue 检出加一行；空 row ≠ 没问题，
是"以前没撞过"）：

| Issue signature | Fix target |
|---|---|
| loop-hygiene: last iter verdict is refine | skills/mesh-to-cad/SKILL.md § Reconstruction loop step 5 |
| loop-hygiene: last-iter verdict=refine but notes declare divergence exit | skills/mesh-to-cad/references/output-schemas.md § Git commit conventions (add plateau_via_divergence verdict or amend rule) |
| route.json missing considered_alternative | skills/mesh-to-cad/references/output-schemas.md § route.json |
| notes.md sections reordered / renamed | skills/mesh-to-cad/references/output-schemas.md § notes.md |
| iou < 0.5 AND chamfer < 0.03 | packages/meshscope/src/meshscope/compare.py::iou (bug, not skill) |
| cost > 3× baseline AND cache_hit < 60% | skills/mesh-to-cad/SKILL.md + references (phase 拆分) |
| preservation heuristic warn | skills/mesh-to-cad/SKILL.md § Non-negotiables (structural count) |
| route disagrees with rubric replay | skills/mesh-to-cad/references/routing-rubric.md |

## 如何更新

本 skill 明显比 cvm-{push,pull} 更需要持续演化，因为它检的是 CAD skill 的
**产物形态**——CAD skill 每次改，本 skill 就有可能要跟。四类触发：

1. **新 pilot 病症**（现有 9 类 check 都没覆盖到的漂移）→ 加新 check 段
   （在 § Workflow step 3 加编号）+ § Handoff Iteration playbook 加 row。
2. **CAD product skill 权威 schema 变化**（`output-schemas.md` /
   `compare-metrics.md` / `routing-rubric.md` 改字段或阈值）→ 本 skill
   § Workflow step 2 引用路径同步、对应 check 判据同步。
3. **metric 实现改进**（如 iou 修好了不再反直觉）→ 相应 check 阈值调整；
   Iteration playbook 里对应 fix target 若失效需删/改。
4. **发现 review.md 里的建议不好使**（fix target 指错、fix 描述含糊）→ 直接改
   Iteration playbook。

commit 消息说明触发事件（例："feat(pilot-review): add check for silent structural
drop after chair pilot lost 23 buttons"）。
参见 `.agents/plans/cvm-sync-and-pilot-review.md § Skill 维护原则`。
