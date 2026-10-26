# Pantheon 原六區交付與完整十二環驗收

最近核對：**2026-10-26T01:06:57.503029+00:00**。**Root40245443101 已於00:42:36正式接受 BE `a34a6956384ce8d9afd7e060bfce9d7a23082c90`／FE `ad31927ded9a9ebd99eb436eb7320bc7a97e4065`。原同持倉數值與來源恢復，真UI卻把未知幣別硬標USD；原FE889修正已來源結案但還待真正部署。原49完成，完整L1–L12／父6AC／指定Antigravity TOTAL仍未完成。**

本次只更新原PR6668同三份文件，Zero production delta，父任務與TOTAL維持未完成。原123歷史身份表與原36筆來源保持原樣；新增11筆核實來源共47筆，只表示來源紀錄。00:04:48初稿不倒填，本checkpoint明列後續結案、正式接受與實際UI結果。紙上測試保持`is_real=false`。

| 範圍 | 本次真實讀回與仍欠界線 |
|---|---|
| 正式接受配對 | Root402454／FEgate40247457767／FEdeploy40249112259均SUCCESS；00:42:36 accepted a34/ad319。Root0兩公開GET／16checks與Root153實際publicpair逐值相符。這是實際配對／metadata，不冒稱全57images或所有artifact；原optional FULL／hosted／roundtrip SKIPPED不是PASS，不加每版重跑gate。 |
| 原A/B/C已完成 | 6800 source選取修正已g7 review／CI／merge a1b80／nativeDONE；原runner55錯選已是歷史，不能再列當前阻礙。A402136真唯讀補pre-edit scalar完成，B400808既有完成不重跑，C402211真9loops；原H1/H3v2／frozenfa3／plan／saga／pool／binding／真fill保留。 |
| D修好401仍非業務完成 | D402401 failed401保留；6810/0e18→a34已真sourceDONE，private harness a34在accepted runtime76062上執行D402439，workflowSUCCESS／pytest0且401消失，但business仍PAUSED/noIncident。原39IDs／ABC／9loopobjects完整reference保存，只增一BFFtypedhealth成10records；不是Loop10／11／H4／全12或全39owner-data跨restart。Reconbaseline0、driftchecks0是unmeasured，不是已測零金融drift。 |
| 同持倉數值已恢復 | 原h12／rt-eb295fe2 qty−2，真Source2385=Telemetry/BFF，市值−4770／notional4770；原Oct22asof／Oct23observed_at／ref／整份lineage/calendar與identity保留。新BFF／Telemetry程序後原holding仍在。正式接受後同holding真UI顯示數值與source ok；價格／asof／ref是API證據，不聲稱是畫面欄位。 |
| 幣別顯示仍錯 | 真UI把currency=null的−4770顯示成`-$4,770`，沒有USD或FX依據，故units仍FAIL，不升格全L9金融。原FE889/a19→24534489已review／CI／merge／nativeDONE：有幣別按原幣、未知中性數字＋「幣別未提供」，不改值或建FX服務；真正paired部署／同持倉unitUI仍欠。原holding保留到這步後再E。 |
| 6811證據修正已完成 | 原6810兩BE欄誤填FE和多餘先deploy文字，已由唯一followup6811/000946→4f38426改原同file三欄，真Antigravity2 review、10CI全成功、nativeDONE23:58:43。它不再是sourcepending，也沒有新增errata副本／產品Root／78functional tests重跑。 |
| E來源已交付，實際操作仍欠 | 原E6808同Taskg2 exactbe0經指定審查與CI，00:05:18合併c28fff38，00:06:46 nativeDONE。多加的accepted_release_retire／attestation門檻已刪，原owner／prior-E／stable-state／實際versions／count1／this-chain-zero保留。它是private harness修正，不需另一產品Root；真E操作、H4與全12仍未完成。 |
| 有界UI／保存完成 | 採原Root153真admintest的一個session、六個回傳roles、七筆觀察含兩reload、saved pool/Persona/F12/loginlogout，並非所有business／finance或全角色矩陣。H2真2意見＋1綜合與完整owner結果跨實際程序替換已完成；B同h12兩owner完整JSON跨實際process保存是新增partialAC4，不把抄舊C receipt當全chain保存。原49/Pattern不重開。 |

目前缺漏的原因與修正不能只用commit數描述。原runner把不同component當成同一root checkout，是交付來源選取錯誤，6800與真A/C讀回已解除。D的401則是caller對既有owner credential／tenant契約接錯；harness source a34、runtime76062、FE ad319是三個不同權威身分，修caller不等於改產品auth政策，也不能把FE SHA填進BE欄或要求tests-only修正先部署產品。6811完成的是這筆證據返工。

Portfolio從provider恢復DD到金融持倉可用之間仍有真資料流缺口：MarketMark序列化丟calendar／receipt／lineage，下游各自raw48h重判，unchanged-sample與same-asof去重又在真provenance抵達前return。6809在既有四seams／單一writer修正這條路，現在a34候選API已實證恢復原price/value；沒有提高172800、重造marketdata／fill、加cache／calendar service／第二writer或新route/FE。**同finalaccepted pair真UI已證數值與source，仍有USD錯標；不能升格完整金融／units驗收。**

E返工則是把原「E不改非本chain資料」擴成歷史全配對排序、完整外部deployment再准入及新proof/schema要求，拒絕原本合法的pre-E外部替換；新be0回到原owner真身分、可靠prior-E時間、相關stable state／實際exposed versions與原count／chain-zero。未知或矛盾真owner證據仍保留未驗，optional deployment欄位缺少本身不是新gate。這不是cleanup waiver，不補GitHub verifier／MFA／grant，也不假造H4或自然Incident。

主證據與精確SHA在[evidence.json](evidence/DEV-SIX-AREA-HOSTED-CLOSEOUT-20261002/evidence.json)的`proofs`；新增十一筆逐項canonical來源身份及原E的sourceDONE界線在[source-delivery-ledger.json](evidence/DEV-SIX-AREA-HOSTED-CLOSEOUT-20261002/source-delivery-ledger.json)。舊proof保留日期界線；00:04:48初稿不變，後續事實在本次較新的checkpoint明列。原Root1唯一runtime/business caller，Root0只原三正式報告／指定TOTAL及Portfolio/evidencecoordination，不重送已耗用的模型、Research／Agora／L6/L7／CPU／控制操作。

原parentg17／ownerHumanOps／reviewerAntigravity／6AC／21依賴／3產物、原43＋Pattern6 literal條款、123source rows、H2real2+1與named partialAC4完整保留。原finance `.0027`僅舊0040，與frozenfa3真AG2 ROD `.0325`分開；原datedFULL400055 exact9aeb與rollback/reactivation400078及其當時acknowledged-data範圍保留，後完成H2／新A–E資料的coverage差異仍欠指定TOTAL原義判定。不能把舊往返冒稱76062/a34，也不新增每owner39GET、全角色、每版FULL／roundtrip或forcedpositive/provider gate。

另查明一個交付控制邊界：publisher只比dev SHA，三報告在CI成功後可令nexttick再Root；publisher不自己寫報告，所以只證條件式回饋，不能說已證多輪自主循環。唯一DEV-CLOSEOUT-REPORT-PUBLISH-BOUNDARY-20261026／PR6813/e1e9→f9abe36已正常review／CI／merge／nativeDONE：完整差異恰限原3report paths且真pair一致時回report-only/no-deploy，保留真正servedSHA。只含此精確範圍，不涵蓋其他harness/docs、不是新平台或父依賴；51個puretests不是live no-deploy觀察。

本PR仍draft；原same-chain D/H4/E、適用L11、幣別修正後同持倉UI、其他原條款及真Antigravity TOTAL／最後repo integration未完成。all12／goal／TOTAL均false；本checkpoint只是原三檔證據更新，不是ready、批准或完成交付。

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
