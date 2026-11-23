# 分窗站件离线合签

站端按确切修订发行分窗件。夜班凭实收组在站库关闭后核定，并从正式账按号复领。既有整件流程和仓储生命周期见 README.md。

## 发行和点收

`prepare.issuance.issue_window(revisions, issues, revision_id, window)` 返回原有类型 `IssuedItem`。`window` 为由两个有理数指定的闭窗，类型校验沿用 README.md，允许首末相等。来件固定该发行号、来源修订、声明窗和窗内有效时温依据。窗外原拍可作为窗内插值出处。`reissue(issues, issue_id)` 用原号返回逐字节相同的来件并登记补发。

仍用 `receive.intake.intake(receipts, payload)` 点收。摘要须涵盖声明窗及证据，点收核验内外来源和闭窗声明，并保存此次收到的字节。摘要不符、来源不符或声明非法时按原 `IntakeResult` 拒收方式返回。坏时温依据不能用于签报。分窗件和整件须可区分，旧 `sign` 收到分窗件抛 `ValueError`。新来件与新快照的内部字节布局由实现决定。

## 核定和复领

在 `measure.requests` 提供 `WindowAssessmentRequest(left_receipt_ids, right_receipt_ids, window, left_threshold, right_threshold)` 和 `sign_windows(receipts, reports, request)`。左右实收号组为有序的非空列表或元组，编号为互不重复的正整数。两组都用分窗实收号，组内同一测头，两组为同试验的不同测头，同头可跨修订。参数、选件、来件类型或依据不合法抛 `ValueError`，未知编号沿用 `LookupError`。

一件在其声明窗内没有有效值的时刻声明缺测，窗外无声明。有效值沿用相邻有效原拍间线性插值和孤立有效原拍的口径。只在本次核定闭窗内比较同头各件声明交集，所有交点和正长重叠上的温度及有效性都须一致。窗内确认为缺测而另一件在同点证明有值，属于冲突。遇冲突抛 `ValueError`，不产生定稿或首执。合算时只取两头都能证明有效且分别严格超温的时刻。同头重叠只计一次，保留合格端点，空结果和合法单点核定窗均无正长段，两项时长为零。

成功返回 `CommittedFinal`。`snapshot` 的 `window`、`thresholds`、`segments`、`total_seconds`、`longest_seconds` 和 `dispatch_seconds` 沿用原字段语义和有理数类型。分窗快照的 `provenance.left`、`provenance.right` 各为按请求顺序排列的 `EvidenceSource` 元组，记录所选实收及来源。`provenance.segments` 与 `snapshot.segments` 逐段对应。每条的 `left` 和 `right` 各为 `(receipt_id, pats)` 元组组成的元组，按同侧请求顺序排列。某件只要在该连续段中的任一合格时刻提供有效值，即使仅支持单点，也列入该段。`pats` 为该件在段中实际用到的原拍号，去重并按字面排序。原拍恰在证据时刻时使用该拍，插值使用两端原拍，裁窗点本身不生成拍号。

成功时定稿和首执一并生效。整改单次签存失败后可重签，原定稿及首执保持原样。关闭并重开正式库后，`report.history.retrieve` 读回原定稿及首执，`report.dispatch.read_dispatch_basis` 读回其固定的最长秒数。后收勘误不改已签记录。

## 样例及运行

`samples/window_cases.json` 中 `revisions` 的键是样例别名，`rows` 每行为 `[原拍号, 秒, 温度]`，`null` 表示缺测。每个 `left` 或 `right` 项为 `[修订别名, 声明窗]`，分别发行并点收后用实收号构造请求。样例 `segments` 为 `[起点, 终点, 左闭, 右闭]`，有理数用字符串表示。样例 `uses` 用各组内从零开始的位置代指运行时实收号。`gap` 给出缺窗事故及应签的两段，`agreement` 核对等值重叠、插值截点和真实单拍。`point_conflict` 与 `cool_conflict` 应拒签，`outside_review` 核对冲突判定的适用窗口。

保留原回归，使用样例核验分窗业务。用原有首执写入失败场景检查无半件、重签及重开复领。演示完成分窗发行、实收、关闭站库、核定和复领，展示真实分段、最长时长和实收出处。沿用 `.pointwise-runtime.json` 的版本 1 和 `cli` 类型，`setup` 为准备命令的参数数组列表，可沿用空列表。`verify` 和 `demo` 各为一条命令的参数数组，核验失败以非零退出。
