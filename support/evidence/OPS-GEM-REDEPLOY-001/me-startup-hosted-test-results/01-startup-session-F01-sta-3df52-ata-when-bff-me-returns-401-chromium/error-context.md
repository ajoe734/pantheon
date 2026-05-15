# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: 01-startup-session.spec.ts >> F01 startup session >> does not fall back to mock current-user data when /bff/me returns 401
- Location: e2e/01-startup-session.spec.ts:256:3

# Error details

```
Error: expect(received).not.toMatch(expected)

Expected pattern: not /serving[-\s]?mock|mock data|seed fallback(?! blocked)|資料來源：seed/i
Received string:      "HYBRID
資料來源：live / seed fallback armed
⟁ Pantheon
管理控制台
研究
搜尋策略、Persona、任務…
⌘K
SNAPSHOT DATA
即時
zh-TW
Auth
閉環 OS
控制中心
閉環執行
研究迴圈
執行迴圈
Persona 交易健康
Live Strategy 監測
部署監測
最佳化迴圈
Sentinel 監測
人為介入佇列
核心管理
Strategy 列表
Alpha 工廠
Persona 列表
資金池
排序儀表板
季度調倉
演化方向
研究與治理
研究與實驗
治理審批
路由策略
權限矩陣
記憶治理
協作規則
知識收件匣
事後檢討知識庫
血緣瀏覽
Artifact 與血緣
營運
部署
執行環境
風險中心
事件
任務
告警
審批
能力管理
工具
MCP 伺服器
技能
工作流模板
排程與 Hook
通道
工作室
系統
稽核日誌
系統設定
LEGACY（舊版）
總覽
控制中心

Pantheon 控制中樞 — 統合 KPI、迴圈、Sentinel 與介入佇列。

開啟 Sentinel
重新整理
自治狀態
自治 · 健康
綜合迴圈與 Sentinel findings 的整體判定。
自治 · 健康
執行中迴圈: 0
卡住迴圈: 0
嚴重 Findings: 0
研究迴圈
0
0 執行中迴圈
執行迴圈
0
0 執行中迴圈
最佳化迴圈
0
0 執行中迴圈
未處理 Findings
0
嚴重 Findings
0
待處理介入
0
工作階段
demo
research · zh-TW
研究迴圈
0
開啟迴圈
0 執行中迴圈
目前無 active runs。
執行迴圈
0
開啟迴圈
0 執行中迴圈
目前無 active runs。
最佳化迴圈
0
開啟迴圈
0 執行中迴圈
目前無 active runs。
聚焦:
全部
警告以上
僅嚴重
重大 Sentinel Findings
全部 →
所有迴圈健康，無重大 findings。
阻塞中的人類審核
全部 →
目前無阻塞自治的人類審核。
Persona 執行健康度
Deploy Smoke Persona 2026-05-13 B Persisted
shadow
degraded · 50
Deploy Smoke Persona 2026-05-13
shadow
degraded · 50
Strategy 執行健康度
目前聚焦條件下無項目。
Mock session（tenantId=demo），待 /bff/me 完成後替換。 · 產出時間: 4:59:50 AM"
```

# Page snapshot

```yaml
- generic [ref=e2]:
  - region "Notifications (F8)":
    - list
  - region "Notifications alt+T"
  - generic [ref=e3]:
    - status [ref=e4]:
      - img [ref=e5]
      - generic [ref=e7]: hybrid
      - generic [ref=e8]: 資料來源：live / seed fallback armed
    - banner [ref=e9]:
      - generic [ref=e10]:
        - generic [ref=e11]: ⟁ Pantheon
        - button "管理控制台" [ref=e12] [cursor=pointer]
      - button "研究" [ref=e13] [cursor=pointer]:
        - img [ref=e14]
        - text: 研究
      - button "搜尋策略、Persona、任務… ⌘K" [ref=e16] [cursor=pointer]:
        - img [ref=e17]
        - generic [ref=e20]: 搜尋策略、Persona、任務…
        - generic [ref=e21]: ⌘K
      - generic [ref=e22]:
        - button "待審批" [ref=e23] [cursor=pointer]:
          - img
        - button "未處理告警" [ref=e24] [cursor=pointer]:
          - img
        - button "執行中任務" [ref=e25] [cursor=pointer]:
          - img
        - generic [ref=e26]: SNAPSHOT DATA
        - button "通知" [ref=e27] [cursor=pointer]:
          - img
      - button "即時" [ref=e28] [cursor=pointer]:
        - img
        - generic [ref=e29]: 即時
      - button "zh-TW" [ref=e31] [cursor=pointer]:
        - img
        - text: zh-TW
      - button "auth-error" [ref=e32] [cursor=pointer]:
        - img
        - generic [ref=e33]: Auth
    - generic [ref=e34]:
      - navigation [ref=e35]:
        - generic [ref=e36]:
          - generic [ref=e37]: 閉環 OS
          - list [ref=e38]:
            - listitem [ref=e39]:
              - link "控制中心" [ref=e40] [cursor=pointer]:
                - /url: /management/control-room
                - img [ref=e41]
                - text: 控制中心
            - listitem [ref=e44]:
              - link "閉環執行" [ref=e45] [cursor=pointer]:
                - /url: /management/loops
                - img [ref=e46]
                - text: 閉環執行
            - listitem [ref=e50]:
              - link "研究迴圈" [ref=e51] [cursor=pointer]:
                - /url: /management/loops/research
                - img [ref=e52]
                - text: 研究迴圈
            - listitem [ref=e54]:
              - link "執行迴圈" [ref=e55] [cursor=pointer]:
                - /url: /management/loops/execution
                - img [ref=e56]
                - text: 執行迴圈
            - listitem [ref=e60]:
              - link "Persona 交易健康" [ref=e61] [cursor=pointer]:
                - /url: /management/loops/execution?focus=personas
                - img [ref=e62]
                - text: Persona 交易健康
            - listitem [ref=e67]:
              - link "Live Strategy 監測" [ref=e68] [cursor=pointer]:
                - /url: /management/loops/execution?focus=strategies
                - img [ref=e69]
                - text: Live Strategy 監測
            - listitem [ref=e79]:
              - link "部署監測" [ref=e80] [cursor=pointer]:
                - /url: /management/loops/execution?focus=deployments
                - img [ref=e81]
                - text: 部署監測
            - listitem [ref=e86]:
              - link "最佳化迴圈" [ref=e87] [cursor=pointer]:
                - /url: /management/loops/optimization
                - img [ref=e88]
                - text: 最佳化迴圈
            - listitem [ref=e92]:
              - link "Sentinel 監測" [ref=e93] [cursor=pointer]:
                - /url: /management/sentinel
                - img [ref=e94]
                - text: Sentinel 監測
            - listitem [ref=e96]:
              - link "人為介入佇列" [ref=e97] [cursor=pointer]:
                - /url: /management/interventions
                - img [ref=e98]
                - text: 人為介入佇列
        - generic [ref=e101]:
          - generic [ref=e102]: 核心管理
          - list [ref=e103]:
            - listitem [ref=e104]:
              - link "Strategy 列表" [ref=e105] [cursor=pointer]:
                - /url: /management/strategies
                - img [ref=e106]
                - text: Strategy 列表
            - listitem [ref=e116]:
              - link "Alpha 工廠" [ref=e117] [cursor=pointer]:
                - /url: /management/alpha-factory
                - img [ref=e118]
                - text: Alpha 工廠
            - listitem [ref=e120]:
              - link "Persona 列表" [ref=e121] [cursor=pointer]:
                - /url: /management/personas
                - img [ref=e122]
                - text: Persona 列表
            - listitem [ref=e127]:
              - link "資金池" [ref=e128] [cursor=pointer]:
                - /url: /management/capital
                - img [ref=e129]
                - text: 資金池
            - listitem [ref=e132]:
              - link "排序儀表板" [ref=e133] [cursor=pointer]:
                - /url: /management/ranking
                - img [ref=e134]
                - text: 排序儀表板
            - listitem [ref=e137]:
              - link "季度調倉" [ref=e138] [cursor=pointer]:
                - /url: /management/rebalance
                - img [ref=e139]
                - text: 季度調倉
            - listitem [ref=e144]:
              - link "演化方向" [ref=e145] [cursor=pointer]:
                - /url: /management/evolution
                - img [ref=e146]
                - text: 演化方向
        - generic [ref=e150]:
          - generic [ref=e151]: 研究與治理
          - list [ref=e152]:
            - listitem [ref=e153]:
              - link "研究與實驗" [ref=e154] [cursor=pointer]:
                - /url: /management/experiments
                - img [ref=e155]
                - text: 研究與實驗
            - listitem [ref=e157]:
              - link "治理審批" [ref=e158] [cursor=pointer]:
                - /url: /management/governance
                - img [ref=e159]
                - text: 治理審批
            - listitem [ref=e163]:
              - link "路由策略" [ref=e164] [cursor=pointer]:
                - /url: /management/governance/policies
                - img [ref=e165]
                - text: 路由策略
            - listitem [ref=e169]:
              - link "權限矩陣" [ref=e170] [cursor=pointer]:
                - /url: /management/governance/permissions
                - img [ref=e171]
                - text: 權限矩陣
            - listitem [ref=e174]:
              - link "記憶治理" [ref=e175] [cursor=pointer]:
                - /url: /management/governance/memory
                - img [ref=e176]
                - text: 記憶治理
            - listitem [ref=e186]:
              - link "協作規則" [ref=e187] [cursor=pointer]:
                - /url: /management/governance/consult
                - img [ref=e188]
                - text: 協作規則
            - listitem [ref=e191]:
              - link "知識收件匣" [ref=e192] [cursor=pointer]:
                - /url: /management/knowledge
                - img [ref=e193]
                - text: 知識收件匣
            - listitem [ref=e195]:
              - link "事後檢討知識庫" [ref=e196] [cursor=pointer]:
                - /url: /management/postmortems
                - img [ref=e197]
                - text: 事後檢討知識庫
            - listitem [ref=e200]:
              - link "血緣瀏覽" [ref=e201] [cursor=pointer]:
                - /url: /management/lineage
                - img [ref=e202]
                - text: 血緣瀏覽
            - listitem [ref=e206]:
              - link "Artifact 與血緣" [ref=e207] [cursor=pointer]:
                - /url: /management/artifacts
                - img [ref=e208]
                - text: Artifact 與血緣
        - generic [ref=e212]:
          - generic [ref=e213]: 營運
          - list [ref=e214]:
            - listitem [ref=e215]:
              - link "部署" [ref=e216] [cursor=pointer]:
                - /url: /management/deployments
                - img [ref=e217]
                - text: 部署
            - listitem [ref=e222]:
              - link "執行環境" [ref=e223] [cursor=pointer]:
                - /url: /management/runtimes
                - img [ref=e224]
                - text: 執行環境
            - listitem [ref=e227]:
              - link "風險中心" [ref=e228] [cursor=pointer]:
                - /url: /management/risk
                - img [ref=e229]
                - text: 風險中心
            - listitem [ref=e231]:
              - link "事件" [ref=e232] [cursor=pointer]:
                - /url: /management/incidents
                - img [ref=e233]
                - text: 事件
            - listitem [ref=e235]:
              - link "任務" [ref=e236] [cursor=pointer]:
                - /url: /management/jobs
                - img [ref=e237]
                - text: 任務
            - listitem [ref=e240]:
              - link "告警" [ref=e241] [cursor=pointer]:
                - /url: /management/alerts
                - img [ref=e242]
                - text: 告警
            - listitem [ref=e245]:
              - link "審批" [ref=e246] [cursor=pointer]:
                - /url: /management/approvals
                - img [ref=e247]
                - text: 審批
        - generic [ref=e251]:
          - generic [ref=e252]: 能力管理
          - list [ref=e253]:
            - listitem [ref=e254]:
              - link "工具" [ref=e255] [cursor=pointer]:
                - /url: /management/tools
                - img [ref=e256]
                - text: 工具
            - listitem [ref=e258]:
              - link "MCP 伺服器" [ref=e259] [cursor=pointer]:
                - /url: /management/mcp
                - img [ref=e260]
                - text: MCP 伺服器
            - listitem [ref=e265]:
              - link "技能" [ref=e266] [cursor=pointer]:
                - /url: /management/skills
                - img [ref=e267]
                - text: 技能
            - listitem [ref=e269]:
              - link "工作流模板" [ref=e270] [cursor=pointer]:
                - /url: /management/workflows
                - img [ref=e271]
                - text: 工作流模板
            - listitem [ref=e275]:
              - link "排程與 Hook" [ref=e276] [cursor=pointer]:
                - /url: /management/hooks
                - img [ref=e277]
                - text: 排程與 Hook
            - listitem [ref=e280]:
              - link "通道" [ref=e281] [cursor=pointer]:
                - /url: /management/channels
                - img [ref=e282]
                - text: 通道
            - listitem [ref=e288]:
              - link "工作室" [ref=e289] [cursor=pointer]:
                - /url: /management/studios
                - img [ref=e290]
                - text: 工作室
        - generic [ref=e292]:
          - generic [ref=e293]: 系統
          - list [ref=e294]:
            - listitem [ref=e295]:
              - link "稽核日誌" [ref=e296] [cursor=pointer]:
                - /url: /management/audit
                - img [ref=e297]
                - text: 稽核日誌
            - listitem [ref=e300]:
              - link "系統設定" [ref=e301] [cursor=pointer]:
                - /url: /management/settings
                - img [ref=e302]
                - text: 系統設定
        - generic [ref=e305]:
          - generic [ref=e306]: Legacy（舊版）
          - list [ref=e307]:
            - listitem [ref=e308]:
              - link "總覽" [ref=e309] [cursor=pointer]:
                - /url: /management/overview-legacy
                - img [ref=e310]
                - text: 總覽
      - main [ref=e315]:
        - generic [ref=e316]:
          - generic [ref=e317]:
            - heading "控制中心" [level=1] [ref=e318]
            - paragraph [ref=e319]: Pantheon 控制中樞 — 統合 KPI、迴圈、Sentinel 與介入佇列。
          - generic [ref=e321]:
            - link "開啟 Sentinel" [ref=e322] [cursor=pointer]:
              - /url: /management/sentinel
              - img
              - text: 開啟 Sentinel
            - button "重新整理" [ref=e323] [cursor=pointer]:
              - img
              - text: 重新整理
        - generic [ref=e324]:
          - generic [ref=e325]:
            - generic [ref=e326]:
              - generic [ref=e327]:
                - generic [ref=e328]:
                  - generic [ref=e329]: 自治狀態
                  - generic [ref=e330]:
                    - img [ref=e331]
                    - generic [ref=e333]: 自治 · 健康
                  - generic [ref=e334]: 綜合迴圈與 Sentinel findings 的整體判定。
                - generic [ref=e335]: 自治 · 健康
              - generic [ref=e336]:
                - generic [ref=e337]:
                  - generic [ref=e338]: "執行中迴圈:"
                  - text: "0"
                - generic [ref=e339]:
                  - generic [ref=e340]: "卡住迴圈:"
                  - text: "0"
                - generic [ref=e341]:
                  - generic [ref=e342]: "嚴重 Findings:"
                  - text: "0"
            - generic [ref=e343]:
              - generic [ref=e344]: 研究迴圈
              - generic [ref=e345]: "0"
              - generic [ref=e346]: 0 執行中迴圈
            - generic [ref=e347]:
              - generic [ref=e348]: 執行迴圈
              - generic [ref=e349]: "0"
              - generic [ref=e350]: 0 執行中迴圈
            - generic [ref=e351]:
              - generic [ref=e352]: 最佳化迴圈
              - generic [ref=e353]: "0"
              - generic [ref=e354]: 0 執行中迴圈
          - generic [ref=e355]:
            - generic [ref=e356]:
              - generic [ref=e357]: 未處理 Findings
              - generic [ref=e358]: "0"
            - generic [ref=e359]:
              - generic [ref=e360]: 嚴重 Findings
              - generic [ref=e361]: "0"
            - generic [ref=e362]:
              - generic [ref=e363]: 待處理介入
              - generic [ref=e364]: "0"
            - generic [ref=e365]:
              - generic [ref=e366]: 工作階段
              - generic [ref=e367]: demo
              - generic [ref=e368]: research · zh-TW
          - generic [ref=e369]:
            - generic [ref=e370]:
              - generic [ref=e371]:
                - generic [ref=e372]:
                  - heading "研究迴圈" [level=3] [ref=e373]
                  - generic [ref=e374]: "0"
                - link "開啟迴圈" [ref=e375] [cursor=pointer]:
                  - /url: /management/loops/research
                  - text: 開啟迴圈
                  - img [ref=e376]
              - generic [ref=e379]: 0 執行中迴圈
              - list [ref=e380]:
                - listitem [ref=e381]: 目前無 active runs。
            - generic [ref=e382]:
              - generic [ref=e383]:
                - generic [ref=e384]:
                  - heading "執行迴圈" [level=3] [ref=e385]
                  - generic [ref=e386]: "0"
                - link "開啟迴圈" [ref=e387] [cursor=pointer]:
                  - /url: /management/loops/execution
                  - text: 開啟迴圈
                  - img [ref=e388]
              - generic [ref=e391]: 0 執行中迴圈
              - list [ref=e392]:
                - listitem [ref=e393]: 目前無 active runs。
            - generic [ref=e394]:
              - generic [ref=e395]:
                - generic [ref=e396]:
                  - heading "最佳化迴圈" [level=3] [ref=e397]
                  - generic [ref=e398]: "0"
                - link "開啟迴圈" [ref=e399] [cursor=pointer]:
                  - /url: /management/loops/optimization
                  - text: 開啟迴圈
                  - img [ref=e400]
              - generic [ref=e403]: 0 執行中迴圈
              - list [ref=e404]:
                - listitem [ref=e405]: 目前無 active runs。
          - generic [ref=e406]:
            - img [ref=e407]
            - generic [ref=e409]: "聚焦:"
            - button "全部" [ref=e410] [cursor=pointer]
            - button "警告以上" [ref=e411] [cursor=pointer]
            - button "僅嚴重" [ref=e412] [cursor=pointer]
          - generic [ref=e413]:
            - generic [ref=e414]:
              - generic [ref=e415]:
                - heading "重大 Sentinel Findings" [level=2] [ref=e416]:
                  - img [ref=e417]
                  - text: 重大 Sentinel Findings
                - link "全部 →" [ref=e419] [cursor=pointer]:
                  - /url: /management/sentinel
              - generic [ref=e420]: 所有迴圈健康，無重大 findings。
            - generic [ref=e421]:
              - generic [ref=e422]:
                - heading "阻塞中的人類審核" [level=2] [ref=e423]
                - link "全部 →" [ref=e424] [cursor=pointer]:
                  - /url: /management/interventions
              - generic [ref=e425]: 目前無阻塞自治的人類審核。
          - generic [ref=e426]:
            - generic [ref=e427]:
              - heading "Persona 執行健康度" [level=2] [ref=e428]
              - list [ref=e429]:
                - listitem [ref=e430]:
                  - link "Deploy Smoke Persona 2026-05-13 B Persisted" [ref=e431] [cursor=pointer]:
                    - /url: /management/loops/execution
                  - generic [ref=e432]:
                    - generic [ref=e433]: shadow
                    - generic [ref=e434]: degraded · 50
                - listitem [ref=e435]:
                  - link "Deploy Smoke Persona 2026-05-13" [ref=e436] [cursor=pointer]:
                    - /url: /management/loops/execution
                  - generic [ref=e437]:
                    - generic [ref=e438]: shadow
                    - generic [ref=e439]: degraded · 50
            - generic [ref=e440]:
              - heading "Strategy 執行健康度" [level=2] [ref=e441]
              - list [ref=e442]:
                - listitem [ref=e443]: 目前聚焦條件下無項目。
          - generic [ref=e444]:
            - img [ref=e445]
            - text: "Mock session（tenantId=demo），待 /bff/me 完成後替換。 · 產出時間: 4:59:50 AM"
```

# Test source

```ts
  209 |             const timeout = window.setTimeout(() => {
  210 |               const state = eventSource.readyState;
  211 |               eventSource.close();
  212 |               reject(new Error(`EventSource did not open; readyState=${state}`));
  213 |             }, 10_000);
  214 |
  215 |             eventSource.onopen = () => {
  216 |               window.clearTimeout(timeout);
  217 |               const state = eventSource.readyState;
  218 |               eventSource.close();
  219 |               resolve({ readyState: state, openState: EventSource.OPEN });
  220 |             };
  221 |
  222 |             eventSource.onmessage = (event) => {
  223 |               window.clearTimeout(timeout);
  224 |               const state = eventSource.readyState;
  225 |               eventSource.close();
  226 |               try {
  227 |                 const payload = JSON.parse(event.data);
  228 |                 resolve({
  229 |                   readyState: state,
  230 |                   openState: EventSource.OPEN,
  231 |                   firstMessageType:
  232 |                     typeof payload.type === "string" ? payload.type : undefined,
  233 |                 });
  234 |               } catch {
  235 |                 resolve({ readyState: state, openState: EventSource.OPEN });
  236 |               }
  237 |             };
  238 |
  239 |             eventSource.onerror = () => {
  240 |               if (eventSource.readyState === EventSource.CLOSED) {
  241 |                 window.clearTimeout(timeout);
  242 |                 reject(new Error("EventSource closed before opening"));
  243 |               }
  244 |             };
  245 |           },
  246 |         ),
  247 |       { url: streamUrl },
  248 |     );
  249 |
  250 |     expect(opened.readyState).toBe(opened.openState);
  251 |     if (opened.firstMessageType) {
  252 |       expect(opened.firstMessageType).toMatch(/^system\./);
  253 |     }
  254 |   });
  255 |
  256 |   test("does not fall back to mock current-user data when /bff/me returns 401", async ({
  257 |     page,
  258 |   }) => {
  259 |     expect(strictFallbackMode()).toBe("strict");
  260 |
  261 |     let interceptedMeRequests = 0;
  262 |     const bffRequests: string[] = [];
  263 |     page.on("request", (request) => {
  264 |       const url = request.url();
  265 |       if (url.includes("/bff/")) {
  266 |         bffRequests.push(url);
  267 |       }
  268 |     });
  269 |     await page.route("**/bff/me**", async (route) => {
  270 |       interceptedMeRequests += 1;
  271 |       await route.fulfill({
  272 |         status: 401,
  273 |         contentType: "application/json",
  274 |         body: JSON.stringify({
  275 |           detail: {
  276 |             error: {
  277 |               code: "AUTH_REQUIRED",
  278 |               message: "FE-INT-GATE-B01 injected /bff/me 401",
  279 |               details: {
  280 |                 precondition_failed: "auth_session",
  281 |               },
  282 |             },
  283 |           },
  284 |         }),
  285 |       });
  286 |     });
  287 |
  288 |     const firstBffRequest = page
  289 |       .waitForRequest((request) => request.url().includes("/bff/"), {
  290 |         timeout: 10_000,
  291 |       })
  292 |       .catch(() => null);
  293 |
  294 |     await page.goto(frontendUrl("/"), { waitUntil: "domcontentloaded" });
  295 |     await firstBffRequest;
  296 |     await page.waitForTimeout(2_000);
  297 |
  298 |     const text = await bodyText(page);
  299 |     await test.info().attach("startup-bff-network", {
  300 |       body: JSON.stringify({ interceptedMeRequests, bffRequests }, null, 2),
  301 |       contentType: "application/json",
  302 |     });
  303 |
  304 |     expect(
  305 |       interceptedMeRequests,
  306 |       `${STARTUP_ME_FOLLOW_UP} fixed: startup must request /bff/me at least once before showing user UI`,
  307 |     ).toBeGreaterThan(0);
  308 |     expect(text).toMatch(/\bAuth\b|AUTH_REQUIRED|Sign in required|STRICT TYPED ERROR/i);
> 309 |     expect(text).not.toMatch(SERVING_MOCK_BANNER);
      |                      ^ Error: expect(received).not.toMatch(expected)
  310 |     expect(text).not.toMatch(/op-fe-gate|portfolio_manager|mock operator/i);
  311 |   });
  312 | });
  313 |
```