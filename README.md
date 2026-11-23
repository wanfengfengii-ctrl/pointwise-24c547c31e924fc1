# 河湾热封试验交班簿

河湾软包装材料厂热封试验室用这套 Python 3.11 工作台保存两只测头的冻结记录。记录员保存采样和勘误，质控员对照冻结版并交班，夜班复核员停源后按实收号签报，调度员按定稿的最长连续暴露安排复检。次日可按原定稿号核对当时的派工依据。

## 运行

- `python3 -B demo.py` 在临时目录完成 T17 的采录、交班、离线签报、次日勘误和重开复领。
- `python3 -B check_project.py` 运行完整回归。控制台物化项目时提供此入口。独立导出可直接运行 `python3 -B -m unittest discover`。

## 值班流程与 Python API

三个 SQLite 文件分别用于站库、实收档及正式账。使用 `sqlite3.connect(path)` 的默认事务设置。库连接由调用方关闭，站库连接可共供 `RevisionRepository` 和 `IssueRepository` 使用。操作入口拥有各自的写事务，调用时应完成连接上的其他事务。仓储构造器补建表，重开既有文件保留记录及编号。示例文件展示完整调用。

1. `record.capture.open_trial(trial_id, heads=('A', 'B'))` 新建双头采录稿。`record_observation(draft, head, pat_id, seconds, celsius)` 收入一拍。`None` 表示显式缺测。拍号、试验号和测头号使用非空白字符串。
2. `record.revisions.freeze(revisions, draft, head, parent_id=None)` 返回整数修订号。按提交顺序校验严格递增的时刻及唯一拍号。前版保留，子版可指定同试验同测头父版。`revisions.get(revision_id)` 读回冻结拍及父号。
3. `prepare.online_preview.preview(revisions, left_revision_id, right_revision_id, window, left_threshold, right_threshold)` 返回带两份原拍的站内对照。`window` 是含首末端的二元组，门槛按选件顺序绑定。`result.dwell` 含分段及两项时长。
4. `prepare.issuance.issue(revisions, issues, revision_id)` 返回 `IssuedItem`，其 `issue_id`、`revision_id` 和 `payload` 固定。`reissue(issues, issue_id)` 记录补发并返回原件。原拍表留站，V1 外层携带来源身份和内容摘要，正文交付有效时温关系及原拍引用。发行时无需指定另一件、核定窗或门槛。
5. `receive.intake.intake(receipts, payload)` 核验 V1 外层、内容摘要和内外来源的一致性，记录实收或拒收。成功的 `IntakeResult.receipt_id` 可用于选配。拒收时 `receipt_id` 为 `None`，`reason` 说明原因。相同来件重新点收可得到另一实收号，各号保存本次所收字节。内容摘要用于核对内容完整性。
6. 关闭站库后，以 `measure.requests.sign(receipts, reports, request)` 核定并提交。`AssessmentRequest(left_receipt_id, right_receipt_id, window, left_threshold, right_threshold)` 明确所选两件及参数。仅可配同一试验的不同测头。派生计量依据在核定时检查。返回 `CommittedFinal`，含 `report_id`、`snapshot` 和 `first_receipt`，成功返回时定稿与首执已持久生效。
7. `report.dispatch.read_dispatch_basis(reports, report_id)` 返回该定稿固定的 `longest_seconds`，供调度员决定是否复检。
8. 勘误版另行冻结、发行、实收和签报。可以继续选旧实收号，后到勘误件不会改变旧号的出处或核定结果。
9. `report.history.retrieve(reports, report_id)` 按原号复领定稿、首执及快照中的派工依据。关闭并重开正式库后仍可复领。

## 计量口径

秒数、温度和门槛接受 `int`、`fractions.Fraction` 或 `Fraction` 可读的有理数字符串。使用精确有理数进行插值、求交及持久化。温度为零的拍有效。只有相邻且均有效的拍之间允许线性插值。首末证据之外及显式缺测为无效，孤立有效拍只证明该时刻。

两头在同一时刻均有效且各自严格超过门槛才合格。相接段只在接点合格时合并。快照保存按时间排序的正长连续段和两端开闭，每段分别列出实际使用的两头原拍号，拍号按字面排序。插值使用两端原拍，恰在观测时刻的单点使用该拍。正式出处另保存按选择顺序绑定的实收、发行、修订和试验身份。累计时长为各段长度之和，最长时长为其中最大值。空集合和合法零宽窗均签出两个零。

重复时刻、倒序、重复拍号、异头父版、非法参数和错配抛出 `ValueError`。未知仓储编号抛出 `LookupError`。点收拒绝按 `IntakeResult` 返回。SQLite 写入异常向调用方报告并回滚该操作。失败的核定或签存没有新增定稿及成功首执，旧号仍可领。调用方可在排除写入错误后重新发起签报，每次成功签报分配独立定稿号。

T17 的两头都在闭窗 [0,2] 内高于 10 °C 时，最长和累计均为 2 秒。甲头第 1 秒勘误为 10 °C 后，同窗结果为 [0,1) 和 (1,2]，累计 2 秒、最长 1 秒。原定稿仍读回 2 秒的派工依据。
