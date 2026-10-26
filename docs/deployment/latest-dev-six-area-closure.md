# Pantheon 原六區交付與架構根因盤點

固定資料截止：**2026-10-26T06:33:03Z**。**E402743 與本輪原AC5回滾往返／有界laterACK保存已完成；產品仍是正式accepted Root402536 BE f9／FE245。原同訊號兩筆各完整成交的新異常已證實，原因與版本仍查。真正同fill Incident→EvolutionDecision／H4、適用L11、原條款scope覆核與指定Antigravity TOTAL仍未完成。**

| 範圍 | 本輪已證實與限制 |
|---|---|
| 產品與UI | 原402536 f9／245 accepted、official17及原holding UI13 numeric／誠實未知幣別顯示通過。這是有日期的原UI，不冒稱E後新UI。fill.metadata.currency=TWD、pool.currency=USD、position.currency仍null，三者不能互代或做未證FX加總；不把未知netPnL／fee／FX說PASS。 |
| 真E完成 | E402743 private harness source d834／產品runtime f9／FE245，phasecompleted。H1 revoke200＋postGETrevoked，另真Governance GET revoked v3；原39refs／10records／ABC-D／3decisions／Oct24baseline及非本chain b74保存，fleet1／chain0。原E402634五項清理但H1pending是歷史；目前不再欠E，不以earlyexit冒稱positiveD/H4。 |
| 真AC5完成 | 05:50:07 baseline a34/ad319、05:52:53回同f9/245，兩腿exit0，原402536封存3images／wholeFE／7protectedowners及strict／真viewer通過。這是local HumanOps原helper／lease操作，不是GitHub Actions job，不倒填原402536 workflow的roundtrip SKIPPED。 |
| laterACK邊界 | raw before-main兩筆Teaching400保留；切版前另兩筆真training GET200形成effective20。before／baseline20／final20業務相同，其中17整份decodedbody相同；其餘3僅明列4個meta query timestamp路徑差，不泛化忽略timestamps。額外D evaluation／snapshot兩筆於05:53:40真final後補讀，wholebody保留；baseline20／final22不同，不稱baseline22或重往返。 |
| 來源與原checkpoint分開 | 原47來源追加6816／6815＝49 delivered entries，兩者真Codex review／4required10CI／merge6e8cb03、d834／nativecompleted。原6814 superseded不是完成。原49checkpoints＝43＋Pattern6照原條款完成，與49sourceentries不是同一指標。21原依賴DONE。PR6668仍draft，remote d581前輪CI通過；local48e只rebase到d834而三report bytes未變，本批仍未commit/push。 |

**新異常是同一確切decision出現兩個完整broker order。** 首fill4f1e（10/24）與次fill a426（10/25）同sig929606、run／binding／runtime／fa3／symbol／mssff49／Oct22bar，兩個不同order及brokerorder各fullSELL1、committed，position−1→−2；不是同order partialfill。14項保存事實已證。Root153仍查source／config／版本及持久fence，不能先叫currenttip bug；Root1負責原source／Incident／Evolution處置。沒有造新行情、baseline、threshold、severity、providercase或強迫正例。

架構上，舊D402439當時6statechecks有效、baseline0／drift未測、自然Evolution0candidate、noIncident／10records的結果仍是dated真negative，不能被新發現倒填成positive/H4。f9 scheduled runtime／lag warning報告只引heartbeat單event，獨立healthIncident不能借來補同fill因果。既有canonical Incident→Evolution有manual_review分支，不必先造financialbaseline；但需要真實適用owner／policy／evidence及decision，新的重複成交也不自動完成這條鏈或所有金融假說。完整Alpha018只是replication_gate／handoff_only、fa3 recipe無金融comparator、B626e是別的imitation評估，這些舊基準觀察保留為有界歷史，不再寫成所有Incident都必須新backtest／baseline服務。

E返工原因是原caller統一用OP_A而缺revoke角色，6816改為既有TEST_ADMIN secret名稱先選再lookup，保留JWT／tenant／版本／idempotency／revoke契約；舊測試把directsecret literal數量硬編7，條件選key後為6，令Smoke失敗，最後沿原整合修正而非放寬auth。6814 superseded、6816才真交付。6815只對齊實際CLI catalog的既有fallback，不是新增產品模型能力或provider額度成功證明。

AC5首attempt因ownworktree0775被guard在SSH前拒絕；Root1只還原自己兩個worktree權限、無source diff，待原leaseTTL後才作有紀錄重試。這是遵守guard的修復，失敗保留；不可報成0attempt／0failure，也不需新gate。Root0獨立核7sourceSHA及wholeFE／3images／7owners／20raw，D2只採既有比較，不冒稱Root0重GET。

**先前把「fullfinancial／fee／FX／financejoinedUI／tradecount未完成」當父任務新增必交付，是報告scope錯誤。** 原生10/23更正已exit0撤回，現在明確採用；Capital原U02/U06早已done，parentAC4保存／跨實際process與AC5 acknowledged-data範圍不變。未知財務數值仍不宣稱通過，也不另建fee/FX服務或保留為新gate。原datedFULL400055仍只證9aeb，未有f9新FULL；原同版本FULL條款的scope由指定TOTAL覆核，不拿歷史當f9PASS，也不新增每版FULL／roundtrip／wholeDB／39ownerrestart門檻。

最新另筆A1草稿attempt40278697937官方failure，沒有已採業務receipt，不能稱草稿成功或0partialwrites；Root1已停H1／Alpha／Teaching／provider／runtime等後續。三個Root自己的真question audit未找回isolatedmethod原問，sharedpending不能寫成user未答；後續是待形成工程提案／處置，不推導授權或第五gate。H2/H3/H4原紙上測試同意保留，不重問；H4仍缺真decision內容。

原g17／HumanOps owner／Antigravity reviewer／6AC／21deps／3reports、原43＋Pattern6 literal條款、123歷史身份全文、36source前綴、H2真2＋1及named保存證據、fa3 `.0325`與舊0040 `.0027`的差別全部保留。原9aeb FULL／400078往返歷史不被改寫；current AC5已補上述有界laterACK，不再把該缺口列成未做。all12／goal／指定TOTAL仍false；原Antigravity TOTAL未發動／未pass，本stage只供Root0真覆核，沒有ACK、發布、repo/source交付或產品操作。

主證據與SHA在[evidence.json](evidence/DEV-SIX-AREA-HOSTED-CLOSEOUT-20261002/evidence.json)的`proofs`；逐筆49source身份見[source-delivery-ledger.json](evidence/DEV-SIX-AREA-HOSTED-CLOSEOUT-20261002/source-delivery-ledger.json)。本批從Root0已rebase的local48e／remoteprior d581三檔bytes準備，base是origin/dev d834，尚未套用／commit／push。

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
