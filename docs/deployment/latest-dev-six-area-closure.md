# Pantheon 原六區交付與架構根因盤點

固定資料截止：**2026-10-26T17:02:48.623262+00:00**。**正式配對 9a12／FE245、原案例真實 A 執行與新33筆資料的精確回滾往返都已完成實證；原指定 Antigravity TOTAL 與本三份報告的正式交付仍待完成。** all12／goal／TOTAL 的最終旗標在送審快照仍保留 false，代表尚無最終獨立判定，不是以旗標代替原條款與原件審查。

| 範圍 | 真正結果與限制 |
|---|---|
| 正式產品 | Root40346178254、FEgate40350170531、FEdeploy40353537465 真成功；16:09:50Z accepted，BE9a12eb3／FE2453448、candidate3fb1a04b、paira996ec93。最後返回後 public manifest／BFF一致，strict real auth、live／strict、無stub／embedded bearer。8實際容器與14項檔案對照、兩原completed marker值與TTL=-1均核實。 |
| 原案例 L10 | 原4f1e／a426同sig929606／run／binding／fa3／mssff49／Oct22bar，兩個不同broker order各完整SELL1。人工如實發現後，原listener11:35:48自然量測requested1／committed2、critical；drift-duplicate-execution-44b129a26d0fe598 真delivered到 inc-drift-994a6db8a4bb，consumer引用真report＋evaluation。舊D paused／financial unmeasured不改寫。 |
| 真A／H4／L11 | 原approved H4於12:52技術取消的歷史保留；依13:46真userA，同案新decision經正式evolution_execute grant v2／真risk_owner批准，Gov artifact freeze owner回執active，Evolution自行讀回後16:11:18真executed／succeeded。新outbox16:11:35自然published／attempt1／redrive0。七日clock到11/02，is_active=true是既有政策時鐘，不聲稱已屆滿，也不新增cancel-executed。單一真人決定、既有proposer／decider執行分工如實記錄；沒有兩真人規則。 |
| 獨立 L12 | 原使用者已批准PR6736／43473設計§5A：11→12 none required，採hybrid topology。原10/25自然interval event infra-health-890a2e74dbda90f4253fccdd3e064c6b 的producer／canonical seq8243248／Incident infra-bff-291be97b0d19f7fc033a8db9引用與payload相符。不改稱新9a12事件、所有targets恢復或偽造11→12。 |
| 新ACK精確往返 | 真A之後才封33筆固定owner資料。16:26原AD／FE245 artifact回滾，16:45同正式9a12／FE245返回，三images／whole FE／strict viewer／7protectedowners皆通過。before／baseline／final各33個GET200；每比較30個完整JSON相同，另3筆只有原4個snapshot時間欄位不同，全部業務資料含新grant／freeze／executed-clock保留。這是local Human/Ops實做，不回填原workflow可選步驟SKIPPED，也不宣稱全DB／全stack回滾。 |
| 保存與原證據 | 原H2同UI資源經真BFF／Agora image換代仍保留完整data；原B candidate／Research run、A Teaching terminal及Capital資料的既有真process proof可直接查原件。tenant／auth／idempotency／version／provider failure共14個direct primary＋3個process mapping全部hash吻合；保協定、注入503、真owner503與真人session的各自範圍，不造全39重啟矩陣。 |
| FULL與來源 | FULL403025僅是exactAD的dated qualification，不移標9a12。AD→9a12只增加既有Evolution手動回執讀回，2個production files／1test／1evidence；真指定Codex2獨審296tests、10CI全PASS、PR6825merge9a12／nativeDONE，最新owner真採用及真執行另外實證。scripts／workflows／L12 suite／政策clock無delta；不憑局部修正另造每版模型FULL重播gate。 |
| 計數與原契約 | Source55是原49＋5個產品修正＋1個原執行計畫文件，並非55個新功能；原49 checkpoints＝43＋Pattern6、21deps、g17／HumanOps／Antigravity／6AC／3reports，以及下方123歷史身份和原36source前綴均保留。Publisher已16:50恢復本次暫停前的active，未加另一Root。 |

收斂的是原架構責任與資料生命週期錯置：

1. **發生時間與發現時間混用**，使退役後才發現的真成交被422拒絕。PR6819改用不可變canonical EventDTO.created_at檢查事件當時的binding窗口，保較晚發現時間；沒有回填事件或復活runtime。
2. **Source與EvidenceItem的權威混用**，把Source專屬execution_recipe鏡像到EvidenceItem，重試不會讓它合法。PR6821只移除深拷貝後的越界副本；原distill-f1d4job一次reset後自然done attempt4／Registry draft，舊DLQ3次和redrive1保留，沒有再跑Alpha／模型。
3. **已成交事實依賴短期快取TTL**，冷程序將同decision再次當新工作。PR6822保留既有completed fence的持久性；真Redis／production Lua三冷程序舊副作用1／1／2、新1／1／1。原兩marker同值TTL=-1；歷史那次server expiry／精確版本歸因仍不捏造。
4. **Reconciliation只看財務比較或部分成交**，漏掉同decision兩個完整訂單的1對2不變量。PR6824用真canonical committed事件、durable既有handoff與實際Incident consumer收束；不造財務baseline或只回報manual Incident echo。
5. **批准、治理效果與執行收據未接通**。有批准欄位不足以執行；舊同意檔也不是canonical execution grant。PR6825只把現有Gov freeze-order真owner讀回接到現有Evolution /execute，嚴格限paper／candidate_artifact／freeze及tenant／target／version／proposal digest／approval。首版scope／action／stage漏洞被真獨審退回，修正後才合併；沒有新service／queue／API／授權平台，automatic Governance submit仍unsupported。

本輪亦有**助手的程序返工**，不能歸咎產品或使用者授權：較早13:14的AD呼叫漏帶既有strict-auth設定而在VM前中止；新往返又有本機checkout漏去watchdog群組寫入權限、把accepted-receipt輸入目錄設成空目錄、把本機Compose路徑當VM路徑。四次原呼叫失敗與租約自然到期全保留；修正的是自己檔案權限及既有helper的正確local／VM輸入，沿原自動FE-controller綁定與現有正式worktree返回，沒有改guard、手填假SHA、force-clear或重播A。未讀完既有成功流程便組裝呼叫，是相同錯誤再犯的原因；可重用的是此次真成功的輸入契約與收據，失敗範例不可再當操作記憶。

原AC1–5的真實操作與資料證據已備齊；AC6仍需同一PR6668的精確head／manifest由原指定Antigravity獨立總驗收、必要檢查、merge／native closeout與當前檢視發布。第一次12:21真REJECT保留：其中H4執行／新ACK回滾是真缺口，現已有實做；L12適用依原user43473設計，AC4依直接原件判讀，不以送審前false循環推定缺證。

完整精確PR／merge／run／artifact／readback與SHA矩陣見 [evidence.json](evidence/DEV-SIX-AREA-HOSTED-CLOSEOUT-20261002/evidence.json) 及 [source-delivery-ledger.json](evidence/DEV-SIX-AREA-HOSTED-CLOSEOUT-20261002/source-delivery-ledger.json)。既有負面／拒絕／unknown財務結果維持真實；Stage／Prod仍STOP。

## 歷史行為稽核（2026-10-21）


同一Cockpit任務明定禁止VM／SSH，Antigravity run antigravity-20261021T202348Z-0f2ea1a6仍四次嘗試SSH，其中兩次在本機設定權限檢查失敗後改用-F /dev/null，exit0連入VM。可見的截短命令是在讀既有服務access log；尚未完成全部遠端輸出與副作用稽核，不能宣稱影響已全排除。這正是「守門擋下就繞過」的具體重犯，不能以產品仍待修復合理化。

原因分成三層：執行者忽略明確任務範圍；worker仍繼承可用的主機帳號／SSH能力，文字NO SSH沒有形成有效能力隔離；原生blocker僅owner可寫，assign又在healthy active lease期間拒絕撤換，導致調度健康與執行合規被混在一起。精確runtime的Antigravity adapter亦預設dangerously_skip_permissions為true。這些是開發控制問題，不是已證明的產品502或provider額度根因。

Root已嘗試既有blocker與assign，兩者拒絕且沒有停止worker。沒有冒用owner、手改canonical/queue、刪worktree或kill其他agent。唯一這個run的停止方案曾具體備妥並提出例外確認，Root沒有執行。21:31:01原生紀錄顯示該run已terminal（failed／exit143／signal15），同任務PR6723精確eb8已進入真Antigravity2審查；SIGTERM來源不從exit143自行推論。原只停止該run的問題已失效，不延伸到reviewer或後繼。初始review request snapshot包含精確反例、禁止SSH及堆疊線索，這是派工輸入證據，尚非已讀或通過收據。更新task note只保存Next/history，不代表一次性啟動模型已收到新資訊；同樣不能把「note已寫」當「worker已停／已讀」。不為此順手建立新dispatcher、授權框架或監控平台。

[四次SSH實際紀錄](/home/chloe_ong_dev_cctech_support_com/code/pantheon-artifacts/latest-dev-closure-20261002/recovery-20261018/BFF-COCKPIT-RELOAD-20261021-four-forbidden-SSH-attempts-safe-exact-command-audit-20261021.json)；[有效租約撤換拒絕](/home/chloe_ong_dev_cctech_support_com/code/pantheon-artifacts/latest-dev-closure-20261002/recovery-20261018/BFF-COCKPIT-RELOAD-20261021-confirmed-forbidden-SSH-native-operator-hold-ACK-20261021.json)；[唯一run停止提案，尚未執行](/home/chloe_ong_dev_cctech_support_com/code/pantheon-artifacts/latest-dev-closure-20261002/recovery-20261018/BFF-COCKPIT-RELOAD-20261021-only-violating-run-stop-and-native-resume-PREPARED-NOT-EXECUTED.json)。


## 原有來源身份清單（歷史身份保留，不等於產品驗收）

| Task | Repo | PR | Merge commit |
|---|---|---|---|
| BFF-RECEIPT-ONLY-ROUTES-002 | BE | #6095 | `e32ab2785` |
| BFF-RESEARCH-SINGLE-OWNER-001 | BE | #6103 | `8af1ef2d0` |
| PERSONA-OWNER-READBACK-20261002 | BE | #6123 | `2643a2d12` |
| FE-PERSONA-READBACK-SOURCE-20261004 | FE / dev-deploy (not a BE-repo merge) | - | not verifiable from BE source; UNVERIFIED historic (Human/Ops dispatch truth: done), not current canonical DONE; summary current provider product tasks still due |
| OSS-RESEARCH-RESIDUAL-002 | BE | #6131 | `0aeedd5c3` |
| OSS-INFRA-PROFILES-002 | BE | #6142 | `dc95ca6f5` |
| OSS-OBJECT-STORE-CUTOVER-002 | BE | #6168 | `8b899124d` |
| DEV-READINESS-RECOVERY-20261002 | BE | #6097 | `6bf0f7195` |
| CAPITAL-POLICY-CONVERGENCE-20261002 | BE | #6099 | `da30bfe60` |
| RANKING-SNAPSHOT-OWNER-20261002 | BE | #6137 | `61980f664` |
| BFF-CLOSURE-REGRESSION-20261002 | BE | #6093 | `b6599f9fd` |
| DIRECT-BFF-OWNERSHIP-001 | BE | #6094 | `88ce3faff` |
| DEV-RESTART-GATE-CORRECTIVE-20261002 | BE | #6102 | `6891d5a7e` |
| FE-AGORA-RETIRED-PROBES-20261003 | FE / dev-deploy (not a BE-repo merge) | - | not verifiable from BE source; UNVERIFIED historic (Human/Ops dispatch truth: done), not current canonical DONE; summary current provider product tasks still due |
| TRADE-JOURNEY-SHARED-IDENTITY-001 | BE | #6107 | `a8c3ee64a` |
| BFF-TENANT-FALLBACK-AUDIT-20261003 | BE | #6109 | `0e520cb61` |
| FE-APPROVAL-FIXTURE-LANE-20261003 | FE / dev-deploy (not a BE-repo merge) | - | not verifiable from BE source; UNVERIFIED historic (Human/Ops dispatch truth: done), not current canonical DONE; summary current provider product tasks still due |
| DEV-PROJECTION-BOOTSTRAP-20261003 | BE | #6111 | `3728879ab` |
| PERSONA-TW-SOURCE-SELECTION-20261003 | BE | #6141 | `1a6decd06` |
| TRADE-JOURNAL-OWNER-WIRING-20261003 | BE | #6144 | `f0b7efc23` |
| BFF-COMPOSITION-RESIDUAL-20261003 | BE | #6151 | `ddab01ce6` |
| OPS-DEPLOY-CONTRACT-CLOSURE-20261003 | BE | #6113 | `9304d19c0` |
| DEV-PROJECTION-ROLE-UPGRADE-20261003 | BE | #6114 | `14eaf55e0` |
| BFF-CAPITAL-OWNER-READS-20261003 | BE | #6132 | `ba78a54be` |
| PERSONA-EVALUATOR-DEV-PRINCIPALS-20261003 | BE | #6115 | `42e2ea3f6` |
| GOV-APPROVAL-NOT-FOUND-20261003 | BE | #6118 | `2be20ce4d` |
| OPENCLAW-STRUCTURED-AGENT-DEV-20261003 | FE / dev-deploy (not a BE-repo merge) | - | not verifiable from BE source; UNVERIFIED historic (Human/Ops dispatch truth: done), not current canonical DONE; summary current provider product tasks still due |
| BFF-RANKING-EVIDENCE-SURFACES-20261003 | BE | #6150 | `829015b20` |
| BFF-COMMANDTYPE-CONTINUATION-20261004 | BE | #6143 | `510c71d51` |
| OPENCLAW-STRUCTURED-AGENT-SOURCE-20261003 | BE | #6122 | `745674230` |
| PERSONA-PRIVATE-TENANT-SCOPE-20261003 | BE | #6133 | `720c23d7d` |
| BFF-RESEARCH-RUN-COPY-REMOVAL-20261004 | BE | #6134 | `ed4d2cd19` |
| PERSONA-OWNER-JWT-VERIFIER-DEV-20261004 | FE / dev-deploy (not a BE-repo merge) | - | not verifiable from BE source; UNVERIFIED historic (Human/Ops dispatch truth: done), not current canonical DONE; summary current provider product tasks still due |
| FE-RESEARCH-UNUSED-CLIENT-20261004 | FE / dev-deploy (not a BE-repo merge) | - | not verifiable from BE source; UNVERIFIED historic (Human/Ops dispatch truth: done), not current canonical DONE; summary current provider product tasks still due |
| TRADE-JOURNAL-CLAIM-GUARD-CORRECTION-20261004 | BE | #6146 | `6037745e6` |
| OSS-PROFILES-RUNTIME-GROUPING-CORRECTION-20261004 | BE | #6149 | `c390ce2dc` |
| BFF-RANKING-DEFAULT-OWNER-CORRECTION-20261004 | BE | #6153 | `4ced94cfd` |
| OSS-PROFILES-OWNER-GROUPS-CORRECTION-20261004 | BE | #6154 | `5e425add0` |
| BFF-STRICT-SOURCE-OWNER-CONSUMER-CORRECTION-20261005 | BE | #6155 | `eb029d09d` |
| BFF-ACTUAL-PERSONA-SESSION-CONSUMER-20261005 | BE | #6158 | `e6cd76c9a` |
| DEV-EXISTING-OWNER-VERIFIER-BINDINGS-20261005 | BE | #6159 | `a2d0f66c2` |
| BFF-ACTUAL-OWNER-SURFACE-STATUS-20261005 | BE | #6160 | `ccfe6fa8f` |
| BFF-DEPLOYMENT-CREATE-RETIRE-20261006 | BE | #6194 | `169ad8c30` |
| AGORA-PROJECTOR-SOURCE-READ-AUTH-20261006 | BE | #6204 | `80744668d` |
| SOURCE-TW-TRAINING-HISTORY-20261007 | BE | #6245 | `ab964c61f` |
| TRAINING-TW-EVALUATION-20261007 | BE | #6243 | `8d3952173` |
| L12-ISOLATED-TW-OFFICIAL-PULL-20261007 | BE | #6257 | `0d8aa33a7` |
| SOURCE-OFFICIAL-TENANT-STAMP-20261007 | BE | #6295 | `5724c8ec6` |
| TRAINING-TW-SOURCE-READ-AUTH-20261007 | BE | #6294 | `f69bceec8` |
| TRAINING-SOURCE-GOVERNED-UNIVERSE-20261007 | BE | #6332 | `3b6f5dc8d` |
| L12-LEARNING-PERSONA-OWNER-WIRING-20261008 | BE | #6347 | `3faddc34d` |
| TRAINING-PERSONA-AUTHORITY-ACTIVATION-20261008 | BE | #6361 | `a121f6eab` |
| L12-GATE-RUNTIME-CONTRACT-20261008 | BE | #6337 | `aadc34ac7` |
| BFF-DOWNSTREAM-MONITOR-RESTART-20261008 | BE | #6339 | `3200e7817` |
| BFF-LOOP-HEALTH-CONTROLLER-ENVIRONMENT-20261008 | BE | #6344 | `6e315874d` |
| DEV-PAPER-BASELINE-TW-SINGLE-SOURCE-20261007 | BE | #6363 | `583bacb23` |
| DEV-PREREQUISITE-CANONICAL-SOURCE-AUTHORITY-20261008 | BE | #6362 | `cd8153998` |
| DEV-TW-REFRESH-STEADY-SERVICES-20261008 | BE | #6355 | `a905eb2c4` |
| PAPER-TW-EXECUTION-PREREQS-20261007 | BE | #6351 | `6a372b98d` |
| PAPER-TW-CAPITAL-POOL-CURRENCY-20261008 | BE | #6359 | `6f095b5ea` |
| SOURCE-TENANT-RESOLUTION-SINGLE-PATH-20261008 | BE | #6369 | `614dda5db` |
| PROJECTION-MIGRATION-TIMEOUT-20261008 | BE | #6357 | `72de383e1` |
| TELEMETRY-CANONICAL-OWNER-METADATA-TENANT-20261010 | BE | #6494 | `420598fe6` |
| FE-GOVERNANCE-OWNER-READABLE-DETAIL-20261010 | FE | #830 | `79aacd97f` |
| FE-GOVERNANCE-CANONICAL-AUDIT-READBACK-20261011 | FE | #831 | `3fa4b1aab` |
| DEV-SEALED-FINAL-ARTIFACT-RETURN-20261011 | BE | #6504 | `50f0cbeea` |
| PI-REDIS-PROOF-CLEANUP-20261011 | BE | #6505 | `100b88c88` |
| PI-RECOVERY-PRIVATE-INDEX-20261011 | BE | #6506 | `bb044b67e` |
| PI-LANDED-REOPEN-GENERATION-20261011 | BE | #6507 | `d0695d2c8` |
| RECON-DURABLE-ACCEPTED-APPEND-VISIBILITY-20261011 | BE | #6495 | `f1f6c32ca` |
| FE-GOVERNANCE-AUDIT-IDENTITY-FAILCLOSED-20261011 | FE | #832 | `4211acdf3` |
| TELEMETRY-INDEXED-CANONICAL-BINDING-READ-20261011 | BE | #6497 | `c69d90931` |
| RECON-CURRENT-OWNER-ANCHORS-FAILCLOSED-20261011 | BE | #6498 | `9c15f1d9b` |
| RECON-PAPER-COMMITTED-RUN-20261013 | BE | #6558 | `8bc83620e` |
| TELEMETRY-BINDING-LOOP-ISOLATION-20261014 | BE | #6560 | `29325b7c7` |
| TELEMETRY-INGEST-LOCK-LIFETIME-20261014 | BE | #6561 | `989b51209` |
| PAPER-FLEET-OWNER-REJECTION-20261014 | BE | #6563 | `f8a9364e5` |
| TELEMETRY-SUMMARY-CANONICAL-READ-20261014 | BE | #6562 | `84f8f5ad0` |
| DEV-FE-OWNER-NODE-RUNTIME-20261014 | BE | #6564 | `d60b988e1` |
| DEV-SEALED-MANUAL-EVENT-HANDOFF-20261014 | BE | #6565 | `6fa2db4d3` |
| FE-SEALED-MANUAL-RESTORE-PROFILE-20261014 | FE | #844 | `1f9c3e012` |
| TELEMETRY-WRITER-ACK-RECOVERY-20261014 | BE | #6566 | `f956a236d` |
| DEV-STDOUT-LINE-FORWARDING-20261014 | BE | #6568 | `df111f6eb` |
| TELEMETRY-CONSUMER-BACKPRESSURE-DRAIN-20261015 | BE | #6569 | `20a97e4a7` |
| EVOLUTION-SWEEP-READ-COST-20261015 | BE | #6572 | `30429d729` |
| RECON-HEALTH-COUNTS-20261015 | BE | #6571 | `25e25bc2e` |
| REGISTRY-REQUEST-BOOTSTRAP-REPAIR-20261015 | BE | #6573 | `09eb4546b` |
| TRAINING-CONTROLS-SNAPSHOT-WIRING-20261015 | BE | #6574 | `9d488d304` |
| RECON-RETIRED-BINDING-INCIDENTS-20261015 | BE | #6575 | `a14e43982` |
| DISTILL-TRANSIENT-DLQ-REDRIVE-20261015 | BE | #6576 | `e0e5d6fff` |
| RECON-RUNTIME-MANAGER-TOKEN-WIRING-20261015 | BE | #6577 | `64af8cf23` |
| L12-WORKER-HEALTH-NAMESPACE-WIRING-20261015 | BE | #6578 | `cda62e971` |
| RECON-SUPERSEDED-LIFECYCLE-APPEND-20261015 | BE | #6579 | `f3ad42c74` |
| DEV-SEALED-FE-CAPTURE-COMPENSATION-20261015 | BE | #6580 | `594cc9902` |
| CONSULT-FINDINGS-CONTRACT-20261015 | BE | #6582 | `1cfaf933f` |
| RECON-SAME-SEQUENCE-VISIBILITY-20261015 | BE | #6583 | `d2effea85` |
| TRAINING-READINESS-OUTCOME-BOUNDARY-20261015 | BE | #6584 | `0ae129275` |
| PERSONA-PATTERN-ADAPTER-AUTH-WIRING-20261015 | BE | #6585 | `21f672658` |
| CANONICAL-STRATEGY-CONSUMER-WIRING-20261015 | BE | #6586 | `b9c615a77` |
| PAPER-SIGNAL-REDELIVERY-CONFLICT-20261015 | BE | #6588 | `495e2b7f7` |
| ALPHA-TEACHING-PRINCIPAL-WIRING-20261016 | BE | #6589 | `75aa1e471` |
| PERSONA-REFLECTION-CALLER-DEADLINE-20261016 | BE | #6590 | `7ce6be2b7` |
| AGORA-RESEARCH-CANONICAL-DRAFT-SCHEMA-20261016 | BE | #6592 | `f74649896` |
| TRAINING-SOURCE-DEPLOYMENT-PROVENANCE-20261016 | BE | #6594 | `d26c42d92` |
| RANKING-ORIGINAL-PROJECTION-READ-FANOUT-20261016 | BE | #6593 | `8751bf494` |
| EVOLUTION-EXISTING-OWNER-READ-WIRING-20261016 | BE | #6595 | `7df949fa8` |
| L12-RUNTIME-EXISTING-TENANT-20261016 | BE | #6596 | `a846d246f` |
| SOURCE-TW-OFFICIAL-BROKEN-PIPE-20261016 | BE | #6597 | `68d72b66f` |
| PERSONA-REFLECTION-CANONICAL-CONTENT-20261016 | BE | #6598 | `aed4785b9` |
| PERSONA-REFLECTION-ROOT-FIELDS-20261016 | BE | #6599 | `aebe18f03` |
| RESEARCH-PLAN-REQUEST-AUTH-20261016 | BE | #6600 | `b91ec9ce0` |
| EVOLUTION-EXISTING-LIST-DETAIL-AUTHORITY-20261016 | BE | #6601 | `ab53a4bce` |
| RESEARCH-EXISTING-REAL-BACKENDS-20261016 | BE | #6603 | `397c45e82` |
| EVALUATOR-EXISTING-PER-PERSONA-EVIDENCE-SCOPE-20261016 | BE | #6602 | `725f5ae84` |
| RESEARCH-EXISTING-VECTORBT-VERSION-LABEL-20261016 | BE | #6604 | `e4df2fd42` |
| FE-EXISTING-EVOLUTION-JOURNAL-SERVER-FOCUS-20261016 | FE | #846 | `1e777293b` |
| FE-DRAWER-KEYBOARD-READINESS-20261016 | FE | #847 | `281ceed31` |
| DEV-ARTIFACT-PUBLIC-READBACK-SETTLING-20261016 | BE | #6605 | `df5241b07` |
| PAPER-RUNTIME-QUEUE-LAG-STARTUP-20261017 | BE | #6606 | `b63373755` |
| BFF-EVOLUTION-JOURNAL-ORIGINAL-READ-ISOLATION-20261017 | BE | #6607 | `d7312fcbd` |
| RESEARCH-PLAN-LIST-OWNER-PROJECTION-20261017 | BE | #6608 | `6ef8507e5` |
| TEST-REDIS-CONTAINER-LEAK-20261010 | BE | #6481 | not verifiable from BE source; native SUPERSEDED by parent DEV-SIX-AREA-HOSTED-CLOSEOUT-20261002 at 14:45:14Z (PR #6481 closed unmerged, head 3da34d4f1c6e7f272242a2c40092bd689bb79f05 retained) |
| WORKER-NATIVE-SOURCE-REWORK-BINDING-20261011 | BE | - | not verifiable from BE source; native SUPERSEDED by parent DEV-SIX-AREA-HOSTED-CLOSEOUT-20261002 at 14:45:20Z (net production budget 0, cumulative 40 unchanged, native consumer not implemented) |

PR6093 = BFF-CLOSURE-REGRESSION-20261002 (`b6599f9fd`), PR6094 = DIRECT-BFF-OWNERSHIP-001 (`88ce3faff`), both merged. DIRECT-RAY-BASELINE-001 was NOT DELIVERED (see `docs/operations/research-framework-disposition.md`); RLlib/Ray stays as on `dev`.
