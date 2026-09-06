# Registry 認證邊界前置複驗

取證2026-09-06 01:34–01:40 UTC，baseline471dc5391；新Registry worker同一baseline。
這是既有共用helper的語義驗證，不是聲稱新Registry程式已出現同一缺陷。
原immutableV2 SA/SD要求strictverifiedtenant/actor契約不變，無須另造JWT驗證器或新任務。

## 已驗證的反例

真實`services/runtime_auth_inbound.py:validate_request_auth(required_roles=['operator'])`，
使用只含iss/aud、缺actor/tenant/roles/exp的synthetic有效簽章JWT，並已配置strict、issuer及audience。
獨立agent在15秒硬上限、純memory與隔離side-effect guard下執行，exit0，得到：

```json
{"accepted":true,"actor_id":"internal-api-operator","roles":["operator"],"claim_keys":["aud","iss"],"token_kind":"jwt"}
```

未輸出token/key，未接network/provider/DB或寫檔；首次guard因標準庫尚未預載而停止，
預載後的第二次才得到上述成功執行結果。這是helper反例，不是hosted安全測試或真實權限利用。
Root已獨立核對source SHA256：
`83adc9583fbd8178e1e940eaecd5c02de80cc126f340d9779dd0113325e88e67`。

## 成因與必要驗收

- AuthContext:102沒有tenant欄；verified claims仍在claims。
- _claims_to_context:666–671缺actor會補internal-api-operator；:689–694缺roles會補operator。
- strict只拒structuredtoken；issuer/audience只在配置非空時驗證，exp允許缺省。
- Registry必須拒絕缺失/歧義的verifiedtenant、actor、role與expiry，檢查必要issuer/audience配置，
  不可把合成ctx預設值、任意body/header、permissive或opaque runtime token當authority。
- 保留合法positive、cross-tenant negatives、issuer/audience不符、過期/缺expiry、missingclaim、
  readbackscope mismatch；沿用既有簽章驗證，只補owner-required claim contract。

## 既有transport可重用但有限制

command_adapters/base.py:121–134委派command_executor._get_json/_post_json：目前要求rawtoken，
executor:250/:281無條件加Bearer；非GET轉POST，傳入timeout不會覆蓋此delegatedpath設定。
不能誤以為支援任意PUT/PATCH或doubleBearer，必要helper擴充需先正式artifact-contract。

main的_COMMAND_AUTH_CONTEXT是runtime-only且消耗後移除，不是restartcredential來源；
禁止把bearer塞durablereceipt/evidence。Workshop目前無Authorizationheader，須按已正式交接的
AGORA-CHAIN範圍完成verifiedcaller傳遞；不得用匿名fallback保相容。

## 正式交接與即時進展

上述source限制已於01:37:32透過Human/Ops寫入REGISTRY-STRATEGY-DURABILITY-PREREQUISITE-001，
不改actor/owner/reviewer/artifacts/acceptance。後續實際probe補充仍屬同一既有驗收要求。
01:40worker PID3433923仍running；storage.py已修改、pg_store.py已新增，尚無新commit驗收。
Worker前置依賴曾缺pytest，後續baseline120passed/2warnings/25.91s，不能當成新durability測試通過。
