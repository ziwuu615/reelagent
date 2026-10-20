# 瀹夊叏绛栫暐锛圫ECURITY.md锛?
> 閫傜敤鐗堟湰锛?*1.0.0**
>
> 鏈枃鎻忚堪 ReelAgent 鐨?*瀹為檯瀹夊叏妯″瀷**鈥斺€旀瘡涓€椤归兘鑳藉湪浠ｇ爜閲屾壘鍒颁緷鎹?> 锛堟瘡鑺傞兘鏍囨敞浜嗗搴旀枃浠讹級銆?> 鍑℃槸**褰撳墠涓嶆敮鎸?*鐨勮兘鍔涳紝浼氭槑纭啓鍑恒€屽綋鍓嶄笉鏀寔銆嶏紝涓嶅仛鍚硦琛ㄨ堪銆?
---

## 鐩綍

- [鏀寔鐨勭増鏈琞(#鏀寔鐨勭増鏈?
- [濡備綍鎶ュ憡婕忔礊](#濡備綍鎶ュ憡婕忔礊)
- [濞佽儊妯″瀷涓庡畨鍏ㄨ竟鐣宂(#濞佽儊妯″瀷涓庡畨鍏ㄨ竟鐣?
- [韬唤涓庨壌鏉僝(#韬唤涓庨壌鏉?
- [浼氳瘽瀹夊叏](#浼氳瘽瀹夊叏)
- [CSRF 闃叉姢](#csrf-闃叉姢)
- [绉熸埛闅旂](#绉熸埛闅旂)
- [鍙ｄ护涓庡嚟璇乚(#鍙ｄ护涓庡嚟璇?
- [瀵嗛挜鍔犲瘑涓庤劚鏁廬(#瀵嗛挜鍔犲瘑涓庤劚鏁?
- [HTTP 瀹夊叏鍝嶅簲澶碷(#http-瀹夊叏鍝嶅簲澶?
- [闄愭祦](#闄愭祦)
- [Webhook 瀹夊叏](#webhook-瀹夊叏)
- [閿欒鍝嶅簲涓庝俊鎭硠闇瞉(#閿欒鍝嶅簲涓庝俊鎭硠闇?
- [瀹¤鏃ュ織](#瀹¤鏃ュ織)
- [鐢熶骇鐜鐨勫己鍒堕厤缃甝(#鐢熶骇鐜鐨勫己鍒堕厤缃?
- [宸茬煡鐨勫畨鍏ㄨ€冮噺涓庡緟鏀硅繘椤筣(#宸茬煡鐨勫畨鍏ㄨ€冮噺涓庡緟鏀硅繘椤?
- [鑷磋阿](#鑷磋阿)

---

## 鏀寔鐨勭増鏈?
| 鐗堟湰 | 鎺ュ彈瀹夊叏淇 |
|---|---|
| `1.0.x` | 鉁?|
| `< 1.0.0` | 鉂岋紙涓嶅瓨鍦ㄥ澶栧彂甯冪殑鐗堟湰锛?|

`1.0.0` 鏄湰椤圭洰鐨?*棣栦釜姝ｅ紡鐗堟湰**锛屼箣鍓嶆病鏈夊澶栧彂甯冭繃浠讳綍鐗堟湰锛?鍥犳娌℃湁鍘嗗彶鐗堟湰鐨勭淮鎶ゆ壙璇恒€傚畨鍏ㄤ慨澶嶄細鍙戝竷鍦?`1.0.x` 琛ヤ竵鐗堟湰涓娿€?
---

## 濡備綍鎶ュ憡婕忔礊

**璇蜂笉瑕佸湪鍏紑鐨?Issue 閲屾姭闇插畨鍏ㄦ紡娲炪€?*

### 鎶ュ憡娓犻亾

鍙戦€侀偖浠跺埌閰嶇疆椤?`REEL_SUPPORT_EMAIL` 鎸囧畾鐨勫湴鍧€
锛堥粯璁ゆ槸 `support@reelagent.local`锛夈€?
> 鈿狅笍 **`support@reelagent.local` 鏄崰浣嶅湴鍧€锛屾棤娉曟敹淇°€?*
> 鐢熶骇閮ㄧ讲鏃?*蹇呴』**鎶?`REEL_SUPPORT_EMAIL` 鏀规垚鐪熷疄鐨勯偖绠?> 锛堣 [DEPLOYMENT.md](docs/DEPLOYMENT.md) 绗?2.1 鑺傦級銆?> 濡傛灉浣犳槸鑷墭绠＄敤鎴凤紝璇锋妸鎶ュ憡鍙戠粰浣犺嚜宸辩殑杩愮淮娓犻亾锛?> 濡傛灉浣犳槸浣跨敤瀹樻柟鎵樼鐨勭敤鎴凤紝璇蜂互鏈嶅姟椤甸潰涓婂叕甯冪殑鑱旂郴鏂瑰紡涓哄噯銆?
### 鎶ュ憡鍐呭

璇峰敖閲忓寘鍚細

1. **鍙楀奖鍝嶇殑鐗堟湰**锛堜緥濡?`1.0.0`锛変笌**閮ㄧ讲褰㈡€?*
   锛堣嚜鎵樼 / 鎵樼銆佸崟涓?API 鍓湰 / 澶氬壇鏈€佹槸鍚﹀惎鐢?Stripe锛夈€?2. **婕忔礊绫诲瀷**锛堣秺鏉冭鍙栥€佽璇佺粫杩囥€佹敞鍏ャ€佹嫆缁濇湇鍔°€佷俊鎭硠闇测€︼級銆?3. **瀹屾暣鐨勫鐜版楠?*锛屽寘鎷叿浣撶殑璇锋眰涓庡搷搴旓紙璇峰厛鎶婂瘑閽ャ€丆ookie銆?   鐪熷疄閭銆佺湡瀹炰骇鐗?URL 鏇挎崲鎴愬崰浣嶇锛夈€?4. **褰卞搷鑼冨洿鐨勫垽鏂?*锛氳兘璇诲埌浠€涔堬紵鑳芥敼浠€涔堬紵闇€瑕佷粈涔堝墠缃潯浠?   锛堝凡鐧诲綍锛熸湁 API Key锛熺壒瀹氬椁愶紵锛夈€?5. **PoC**锛堝鏈夛級銆備竴涓兘璺戦€氱殑 PoC 鑳滆繃涓夋鎻忚堪銆?6. **寤鸿鐨勪慨澶嶆柟鍚?*锛堝彲閫夛紝浣嗗緢鏈夊府鍔╋級銆?
### 鎴戜滑鐨勫搷搴?
- 鎴戜滑浼氬湪**灏藉姏鑰屼负**鐨勫墠鎻愪笅纭鏀跺埌骞剁粰鍑哄垵姝ヨ瘎浼般€?  **鏈枃涓嶆壙璇哄叿浣撶殑鍝嶅簲鏃堕棿 SLA**鈥斺€旇繖鏄竴涓皻鏈笂绾胯繍钀ョ殑寮€婧愰」鐩紝
  娌℃湁 7脳24 鐨勫畨鍏ㄥ搷搴斿洟闃熴€?- 濡傛灉浣犻渶瑕佸崗鍚屾姭闇诧紙coordinated disclosure锛夛紝璇峰湪鎶ュ憡閲岃鏄庛€?  鎴戜滑鐨勯粯璁ゅ仛娉曟槸锛?  1. 鍏堝湪绉佹湁娓犻亾淇骞跺彂甯冭ˉ涓佺増鏈紱
  2. 琛ヤ竵鍙戝竷鍚庡啀鍏紑鑷磋阿锛堝鏋滀綘鎰挎剰琚嚧璋級銆?- 璇峰湪琛ヤ竵鍙戝竷鍓?*涓嶈鍏紑婕忔礊缁嗚妭**銆?
### 涓嶅湪鑼冨洿鍐呯殑涓滆タ

浠ヤ笅閫氬父**涓嶈涓哄畨鍏ㄦ紡娲?*锛堜絾娆㈣繋浣滀负鏅€?Issue 鎶ュ憡锛夛細

- 缂哄皯鏌愪釜鎴戜滑鏄庣‘澹版槑銆屽綋鍓嶄笉鏀寔銆嶇殑鍔熻兘锛堜緥濡?2FA銆乄AF銆両P 鐧藉悕鍗曪級銆?- 闇€瑕佹敾鍑昏€呭凡缁忔嫢鏈夋暟鎹簱鎴栧涓绘満璁块棶鏉冮檺鐨勬敾鍑诲満鏅€?- 鑷墭绠￠儴缃蹭腑琚敤鎴疯嚜宸辨敼鍧忕殑閰嶇疆锛堜緥濡傛妸搴旂敤绔彛鏆撮湶鍒板叕缃戙€?  娌℃湁閰嶇疆鍙嶅悜浠ｇ悊銆乣REEL_SECURITY__ALLOWED_HOSTS` 鐣欐垚 `*`锛夈€?- 宸叉槑纭褰曞湪 [CHANGELOG 宸茬煡闂](CHANGELOG.md#宸茬煡闂)閲岀殑鏉＄洰銆?- 渚濊禆搴撹嚜韬殑婕忔礊锛堣鎶ュ憡缁欎笂娓革紝鍚屾椂涔熸杩庡憡鐭ユ垜浠互渚垮崌绾т緷璧栵級銆?
---

## 濞佽儊妯″瀷涓庡畨鍏ㄨ竟鐣?
### 璧勪骇

| 璧勪骇 | 涓轰粈涔堥噸瑕?|
|---|---|
| **棰濆害浣欓涓庤处鏈?* | 鐩存帴瀵瑰簲閽?|
| **Stripe 瀹㈡埛涓庤闃?* | 璐㈠姟鎺у埗鏉?|
| **缁勭粐鐨勪緵搴斿晢 API Key锛圔YOK锛?* | 鍙鐢ㄦ潵娑堣€楃敤鎴疯嚜宸辩殑棰濆害 |
| **鐢ㄦ埛绱犳潗涓庣敓鎴愪骇鐗?* | 鐢ㄦ埛鐨勫垱浣滃唴瀹癸紝鍙兘鍚湭鍏紑鐨勫晢涓氫俊鎭?|
| **缁勭粐鎴愬憳鍒楄〃** | 鐢ㄤ簬瀹氬悜閽撻奔涓庢潈闄愭彁鍗?|
| **骞冲彴鑷繁鐨勪緵搴斿晢 Key** | 娉勬紡浼氬鑷寸洿鎺ョ殑璧勯噾鎹熷け |

### 淇′换杈圭晫

```
鈹屸攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹?鈹?涓嶅彲淇″尯鍩燂細鍏綉銆佹祻瑙堝櫒銆丆I 鐜銆佺涓夋柟闆嗘垚                     鈹?鈹斺攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹?                            鈹?HTTPS锛圱LS 鐢卞弽鍚戜唬鐞嗙粓姝級
鈹屸攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈻尖攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹?鈹?杈圭晫 1锛氬弽鍚戜唬鐞嗭紙蹇呴』鐢遍儴缃叉柟淇濊瘉锛?                             鈹?鈹?  路 搴旂敤绔彛涓嶅鍏綉寮€鏀撅紙闃?X-Forwarded-For 浼€狅級              鈹?鈹?  路 涓嶆敼鍐欒姹備綋锛圫tripe 楠岀渚濊禆鍘熷浣擄級                        鈹?鈹?  路 涓嶆妸 5xx 閲嶅啓鎴?200锛圫tripe 閲嶈瘯渚濊禆锛?                     鈹?鈹斺攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹?鈹屸攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈻尖攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹?鈹?杈圭晫 2锛氳璇侊紙anonymous / session / api_key锛?                   鈹?鈹?  路 浼氳瘽绛惧悕鏍￠獙 + 浼氳瘽浠ｉ檯 + 閿佸畾                               鈹?鈹?  路 API Key 鍝堝笇姣斿 + scope + 寮哄埗闄嶇骇涓?member                 鈹?鈹斺攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹?鈹屸攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈻尖攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹?鈹?杈圭晫 3锛氭巿鏉冿紙require_role / require_session / require_scope锛?   鈹?鈹?  路 API Key 涓嶈兘绠¤璐逛笌鎴愬憳                                     鈹?鈹斺攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹?鈹屸攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈻尖攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹?鈹?杈圭晫 4锛氱鎴烽殧绂伙紙鏌ヨ灞?org 杩囨护 + 瀛樺偍灞?key 鍓嶇紑鏍￠獙锛?         鈹?鈹斺攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹?鈹屸攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈻尖攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹?鈹?鍙俊鍖哄煙锛氭暟鎹簱銆佸璞″瓨鍌ㄣ€佷緵搴斿晢 Key銆亀orker                   鈹?鈹斺攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹?```

**鏈€鍏抽敭鐨勯儴缃插亣璁?*锛?*杈圭晫 1 鐢遍儴缃叉柟淇濊瘉锛屽簲鐢ㄦ棤娉曡嚜璇併€?*
鍏蜂綋鏉ヨ锛屽鏋滃簲鐢ㄧ鍙ｆ毚闇插埌鍏綉锛屾敾鍑昏€呭彲浠ワ細

- 浼€?`X-Forwarded-For` 缁曡繃鎵€鏈夊熀浜?IP 鐨勯檺娴?  锛坄anonymous` / `login` / `signup` 涓変釜 scope锛夈€?- 缁曡繃 TLS锛堝鏋滀唬鐞嗘病鍋氬己鍒惰烦杞級銆?
杩欎笉鏄簲鐢ㄧ殑 bug锛岃€屾槸閮ㄧ讲閰嶇疆闂銆傝瑙?[DEPLOYMENT.md](docs/DEPLOYMENT.md) 绗?4.4 鑺傘€?
### 鏄庣‘鐨勪俊浠诲亣璁?
| 鍋囪 | 濡傛灉涓嶆垚绔嬬殑鍚庢灉 |
|---|---|
| 鍙嶅悜浠ｇ悊鍙俊涓斿簲鐢ㄧ鍙ｄ笉瀵瑰 | IP 闄愭祦鍙缁曡繃 |
| `REEL_SECURITY__SECRET_KEY` 淇濆瘑 | 鍙吉閫犱换鎰忕敤鎴风殑浼氳瘽銆佽В瀵?BYOK 瀵嗛挜銆佷吉閫犲垎浜摼鎺?|
| 鏁版嵁搴撲笉鍙澶栭儴鐩存帴璁块棶 | 鍏ㄩ儴鏁版嵁娉勬紡 |
| 渚涘簲鍟?Key 鍙湪鏈嶅姟绔娇鐢?| 璧勯噾鎹熷け |
| 瀹夸富鏈轰笌瀹瑰櫒闀滃儚鏈鍏ヤ镜 | 瀹屽叏澶遍櫡 |

---

## 韬唤涓庨壌鏉?
**浠ｇ爜**锛歚reelagent_cloud/security/deps.py`銆乣security/tokens.py`

### 涓夌韬唤

| 韬唤 | 鍒ゅ畾渚濇嵁 | 鑳藉姏 |
|---|---|---|
| **鍖垮悕** | 鏃犲嚟璇?| 钀藉湴椤点€佸畾浠烽〉銆佸叕寮€鐘舵€侀〉銆乣/api/v1/plans`銆佺郴缁熸帰閽?|
| **娴忚鍣ㄤ細璇?* | `reelagent_session` Cookie | 瀹屾暣浜у搧鑳藉姏锛堝惈璁¤垂涓庢垚鍛樼鐞嗭級 |
| **API Key** | `Authorization: Bearer reel_...` 鎴?`X-API-Key` | 璇诲啓椤圭洰锛?*涓嶈兘绠¤璐逛笌鎴愬憳** |

### API Key 鐨勮兘鍔涜竟鐣岋紙鏈€閲嶈鐨勮璁″喅绛栵級

```python
def require_role(self, minimum: MembershipRole) -> None:
    if self.kind != "session":
        raise Forbidden("璇ユ搷浣滈渶瑕佷娇鐢ㄦ祻瑙堝櫒鐧诲綍锛圓PI Key 涓嶅叿澶囩鐞嗘潈闄愶級")
    if self.role.rank < minimum.rank:
        raise Forbidden(f"闇€瑕?{minimum.value} 鍙婁互涓婃潈闄?)
```

**API Key 涓€寰嬫棤娉曢€氳繃 `require_role`**鈥斺€旀棤璁哄畠鐨?`scopes` 閲屽啓浜嗕粈涔堛€?
骞朵笖 API Key 鐨勮鑹茶**寮哄埗闄嶇骇涓?`member`**锛?
```python
@property
def role(self) -> MembershipRole:
    if self.kind == "api_key":
        return MembershipRole.MEMBER
```

**涓轰粈涔堜笉缁ф壙鍒涘缓鑰呯殑瑙掕壊**锛氬惁鍒欑鐞嗗憳绂昏亴鍓嶅垱寤虹殑 Key 浼氭案涔呬繚鐣?绠＄悊鍛樿兘鍔涳紝鍗充娇璇ョ鐞嗗憳鍚庢潵琚檷鏉冩垨绉婚櫎銆?
**鐞嗙敱**锛堜唬鐮佹敞閲婂師璇濓級锛?
> API Key 浼氬嚭鐜板湪 CI銆佽剼鏈€佺涓夋柟闆嗘垚閲岋紝娉勬紡姒傜巼杩滈珮浜庢祻瑙堝櫒浼氳瘽銆?> 鎶婂畠闄愬埗鍦ㄣ€岃鍐欓」鐩€嶈寖鍥村唴锛屽嵆浣挎硠婕忎篃涓嶄細瀵艰嚧璐﹀崟琚敼鎴栨垚鍛樿韪€?
瀹屾暣鐨勫喅绛栬褰曡 [ADR 0004](docs/adr/0004-api-key-cannot-manage-billing.md)銆?
### 鎺堟潈渚濊禆鐨勬纭娇鐢?
| 渚濊禆 | 鍏佽鐨勮韩浠?| 搴斾娇鐢ㄤ簬 |
|---|---|---|
| `OptionalPrincipal` | 鍖垮悕 + 浼氳瘽 + API Key | 鍏紑椤甸潰 |
| `CurrentPrincipal` | 浼氳瘽 + API Key | 鍙绔偣 |
| `WritablePrincipal` | 浼氳瘽 + API Key锛堥渶 `write`锛?| 鍐欓」鐩殑绔偣 |
| `SessionPrincipal` | **浠呬細璇?* | 鎺у埗鍙伴〉闈€佽璐?|
| `AdminPrincipal` | 浠呬細璇?+ role 鈮?admin | 鎴愬憳銆佸瘑閽ャ€丅YOK |
| `OwnerPrincipal` | 浠呬細璇?+ role = owner | 楂橀闄╂搷浣?|

### 璁よ瘉澶辫触鐨勮褰?
姣忔璁よ瘉澶辫触閮戒細鎵撴寚鏍?`reelagent_auth_failures_total{reason=...}`锛?`reason` 鍙栧€硷細

| `reason` | 鍚箟 |
|---|---|
| `malformed_api_key` | Key 鏍煎紡涓嶅悎娉曪紙娈垫暟/鍓嶇紑/闀垮害涓嶅锛?|
| `invalid_api_key` | Key 涓嶅瓨鍦ㄦ垨宸插け鏁?|
| `org_suspended` | 缁勭粐琚仠鐢?|
| `invalid_session` | 浼氳瘽绛惧悕鏃犳晥鎴栧凡杩囨湡 |
| `user_inactive` | 鐢ㄦ埛琚仠鐢?|
| `stale_session_version` | **浼氳瘽浠ｉ檯涓嶅尮閰?*锛堟敼瀵嗘垨鐧诲嚭鎵€鏈夎澶囧悗鐨勬棫 Cookie锛?|
| `csrf_mismatch` | CSRF 鏍￠獙澶辫触 |
| `internal_error` | 璁よ瘉鐜妭鍐呴儴閿欒 |

**璁よ瘉鐜妭缁濅笉鍥犲唴閮ㄩ敊璇斁琛?*锛?
```python
except Exception as exc:  # pragma: no cover - 璁よ瘉鐜妭缁濅笉鍥犲唴閮ㄩ敊璇斁琛?    log.warning("auth_unexpected_error", error=str(exc))
    auth_failures_total.labels(reason="internal_error").inc()
    return None
```

### 鐧诲綍澶辫触鐨勯攣瀹?
| 鍙傛暟 | 鍊?|
|---|---|
| 閿佸畾闃堝€?| `LOCK_THRESHOLD = 8` 娆¤繛缁け璐?|
| 閿佸畾鏃堕暱 | `LOCK_MINUTES = 15` 鍒嗛挓 |

閿佸畾鍚庤繑鍥?`account_locked`锛?03锛夛紝娑堟伅鍚墿浣欏垎閽熸暟锛?
```
鐧诲綍澶辫触娆℃暟杩囧锛岃 N 鍒嗛挓鍚庨噸璇?```

鐧诲綍鎴愬姛浼?*娓呴浂** `failed_login_count` 骞舵竻绌?`locked_until`銆?
### 鎶楃敤鎴峰悕鏋氫妇

鐢ㄦ埛涓嶅瓨鍦ㄦ椂**涔熸墽琛屼竴娆″搱甯屾牎楠?*锛?
```python
if user is None:
    verify_password(password, hash_password("dummy-password-for-timing"))
    auth_failures_total.labels(reason="unknown_user").inc()
    raise Unauthorized("閭鎴栧瘑鐮佷笉姝ｇ‘")
```

骞朵笖鐧诲綍澶辫触鏃?*瀵广€岀敤鎴蜂笉瀛樺湪銆嶄笌銆屽瘑鐮侀敊璇€嶈繑鍥炵浉鍚岀殑娑堟伅**
锛堛€岄偖绠辨垨瀵嗙爜涓嶆纭€嶏級锛岄伩鍏嶉€氳繃娑堟伅宸紓鏋氫妇宸叉敞鍐岄偖绠便€?
> 鈩癸笍 **娈嬬暀鐨勬灇涓鹃潰**锛歚user is None` 鍒嗘敮閲岀殑
> `hash_password("dummy-password-for-timing")` 姣忔閮戒細**閲嶆柊璁＄畻涓€娆″搱甯?*
> 锛堣€屼笉鏄敤棰勫厛绠楀ソ鐨勫父閲忥級锛屽洜姝よ鍒嗘敮鐨勮€楁椂浼氬寘鍚竴娆″搱甯岀敓鎴愶紝
> 姣斻€岀敤鎴峰瓨鍦ㄤ絾瀵嗙爜閿欒銆嶇殑鍒嗘敮鏇存參銆?> 杩欑悊璁轰笂浠嶅瓨鍦ㄤ竴涓彲娴嬮噺鐨勬椂搴忓樊寮傘€?> 鏇村ソ鐨勫仛娉曟槸鐢ㄤ竴涓ā鍧楃骇棰勮绠楃殑甯搁噺鍝堝笇銆?
### 褰撳墠涓嶆敮鎸佺殑璁よ瘉鑳藉姏

- 鉂?**鍙屽洜绱犺璇侊紙2FA / TOTP锛?*
- 鉂?**OAuth / SSO 鐧诲綍**
- 鉂?**缁勭粐绾ф湇鍔¤处鍙?*锛堢嫭绔嬩簬 API Key 鐨勬満鍣ㄨ韩浠斤級
- 鉂?**鐧诲綍璁惧绠＄悊**锛堟棤娉曟煡鐪?鍗曠嫭鍚婇攢鏌愪釜璁惧鐨勪細璇濓紝
  鍙兘銆岀櫥鍑烘墍鏈夎澶囥€嶏級

---

## 浼氳瘽瀹夊叏

**浠ｇ爜**锛歚security/tokens.py`銆乣security/deps.py`銆?`api/routes/auth.py`

### 浼氳瘽浠ょ墝

鐢?`itsdangerous.URLSafeTimedSerializer` 绛惧悕锛宻alt 涓?`reelagent.session.v1`锛?
```python
SESSION_SALT = "reelagent.session.v1"

def issue_session(settings, *, user_id: str, session_version: int) -> str:
    payload = {"uid": user_id, "sv": int(session_version), "iat": int(time.time())}
    return _serializer(settings, SESSION_SALT).dumps(payload)
```

**杞借嵎鍒绘剰鍙湁涓変釜瀛楁**锛?
| 瀛楁 | 鍚箟 |
|---|---|
| `uid` | 鐢ㄦ埛 ID |
| `sv` | 浼氳瘽浠ｉ檯 |
| `iat` | 绛惧彂鏃堕棿鎴?|

**涓轰粈涔堜笉鏀捐鑹?/ 棰濆害 / 濂楅**锛堜唬鐮佹敞閲婂師璇濓級锛?
> 涓嶆斁瑙掕壊 / 棰濆害 / 濂楅鈥斺€旈偅浜涘繀椤绘瘡娆′粠搴撻噷璇伙紝鍚﹀垯鏉冮檺鍙樻洿鏃犳硶鍗虫椂鐢熸晥銆?
**璇诲彇鏃舵牎楠?*锛?
```python
data = _serializer(settings, SESSION_SALT).loads(
    token, max_age=settings.security.session_max_age_seconds
)
```

鍥犳杩囨湡鐢?itsdangerous 鐨?`max_age` 寮哄埗锛堥粯璁?14 澶╋級銆?
### 浼氳瘽浠ｉ檯锛圫ession Generation锛?
`users.session_version` 鏄竴涓暣鏁帮紝榛樿 `1`銆?*浠讳綍宸茬鍙?Cookie 閲岄兘甯︾潃
绛惧彂鏃剁殑 `sv` 鍊硷紝鏈嶅姟绔瘡娆¤姹傞兘姣斿**锛?
```python
if int(data.get("sv", 0)) != int(user.session_version):
    auth_failures_total.labels(reason="stale_session_version").inc()
    return None
```

**瑙﹀彂 +1 鐨勬搷浣?*锛?
| 鎿嶄綔 | 浣嶇疆 |
|---|---|
| 鏀瑰瘑鐮?| `accounts.change_password()` |
| 鐧诲嚭鎵€鏈夎澶?| `accounts.logout_all_devices()` |
| 锛坄POST /logout-all` 璋冪敤涓婇潰鐨勫嚱鏁帮級 | `api/routes/auth.py` |

**浠峰€?*锛氫笉闇€瑕佺淮鎶や竴寮犳湇鍔＄ session 琛紝灏辫兘鍋氬埌銆屾壒閲忓悐閿€鍏ㄩ儴浼氳瘽銆嶃€?杩欐槸銆屾棤鐘舵€佷細璇?+ 鍙壒閲忓悐閿€銆嶇殑鎶樹腑鏂规銆?
> 鈿狅笍 **娉ㄦ剰锛氭櫘閫氱殑 `POST /logout` 涓嶄細 +1**锛屽畠鍙槸娓呴櫎褰撳墠娴忚鍣ㄧ殑 Cookie銆?> 鍥犳濡傛灉鏈変汉宸茬粡澶嶅埗浜嗕綘鐨?Cookie锛屼綘鐐广€岀櫥鍑恒€?*涓嶄細**璁╅偅浠藉壇鏈け鏁堛€?> 瑕佺湡姝ｆ壒閲忓悐閿€蹇呴』鐢ㄣ€岀櫥鍑烘墍鏈夎澶囥€嶏紙`POST /logout-all`锛夈€?>
> 杩欎篃鎰忓懗鐫€锛?*鏀瑰瘑鐮佹槸鍞竴浼氳嚜鍔ㄥけ鏁堟墍鏈夊叾瀹冧細璇濈殑甯歌鎿嶄綔**
> 锛坄change_password` 浼?+1锛夈€?
### Cookie 灞炴€?
| Cookie | HttpOnly | SameSite | Secure | Path | max_age |
|---|---|---|---|---|---|
| `reelagent_session` | 鉁?| `Lax` | 璺熼殢 `https_only` | `/` | 14 澶?|
| `reelagent_csrf` | 鉂岋紙鍓嶇闇€璇诲彇锛?| `Lax` | 璺熼殢 `https_only` | `/` | 14 澶?|

**`SameSite=Lax` 鐨勫彇鑸?*锛?
- 闃绘璺ㄧ珯 `POST`锛堣繖鏄?CSRF 鐨勪富瑕佽浇浣擄級銆?- 淇濈暀浠庡閮ㄩ摼鎺ヨ繘鏉ョ殑鍙敤鎬э紙椤剁骇瀵艰埅浼氬甫涓?Cookie锛?  鍥犳鐢ㄦ埛浠庨偖浠?IM 閲岀偣閾炬帴杩涙潵浠嶆槸鐧诲綍鐘舵€侊級銆?
**`Secure` 鐢?`REEL_SECURITY__HTTPS_ONLY` 鎺у埗**锛?鐢熶骇鐜蹇呴』璁句负 `true`锛堝惎鍔ㄦ牎楠屼細寮哄埗锛夈€?
**HttpOnly 鐨勬剰涔?*锛氫細璇?Cookie 鏃犳硶琚?JavaScript 璇诲彇锛?鍥犳涓€涓?XSS 婕忔礊涓嶈兘鐩存帴鍋疯蛋浼氳瘽浠ょ墝
锛堜絾浠嶅彲浠ュ湪椤甸潰閲屼唬鏇跨敤鎴峰彂璧疯姹傗€斺€旀墍浠?CSP 涔熷緢閲嶈锛夈€?
### 浼氳瘽鍥哄畾锛圫ession Fixation锛?
**鐧诲綍涓庢敞鍐岄兘浼氱鍙戝叏鏂扮殑浼氳瘽浠ょ墝**锛堜笉娌跨敤璇锋眰閲屽甫鐨勪换浣?Cookie锛夛紝
鍥犳涓嶅瓨鍦ㄤ細璇濆浐瀹氶棶棰樸€俙_set_session_cookies()` 鍚屾椂鍐欏叆
涓€涓柊鐨?`reelagent_session` 鍜屼竴涓?*鍏ㄦ柊鐨?* CSRF 浠ょ墝銆?
### 褰撳墠涓嶆敮鎸佺殑鑳藉姏

- 鉂?**鏈嶅姟绔?session 琛?*锛堟棤娉曞崟鐙悐閿€鏌愪釜浼氳瘽锛?- 鉂?**浼氳瘽 IP / UA 缁戝畾**锛圕ookie 琚鍒跺悗鍙湪鍒浣跨敤锛?- 鉂?**鍒锋柊浠ょ墝涓庣煭鏈熻闂护鐗屽垎绂?*

---

## CSRF 闃叉姢

**浠ｇ爜**锛歚security/deps.py::verify_csrf`銆乣security/tokens.py`銆?`api/routes/console.py::_require_csrf`

### 鍙屾彁浜?Cookie锛圖ouble Submit Cookie锛?
**Cookie 渚?*锛堥潪 HttpOnly锛屽墠绔彲璇伙級锛?
```
reelagent_csrf=<token_urlsafe(32)>
```

**璇锋眰澶翠晶**锛?
```http
x-csrf-token: <涓?Cookie 瀹屽叏鐩稿悓鐨勫€?
```

**鏈嶅姟绔瘮瀵?*锛岀敤甯告暟鏃堕棿姣旇緝閬垮厤鏃跺簭渚т俊閬擄細

```python
def csrf_matches(cookie_value: str, header_value: str) -> bool:
    """甯告暟鏃堕棿姣旇緝锛岄伩鍏嶆椂搴忎晶淇￠亾銆?""
    if not cookie_value or not header_value:
        return False
    return hmac.compare_digest(cookie_value, header_value)
```

### 閫傜敤鑼冨洿

```python
async def verify_csrf(request, principal, settings) -> None:
    if principal.kind != "session":
        return                                          # API Key 璺宠繃
    if request.method in {"GET", "HEAD", "OPTIONS", "TRACE"}:
        return                                          # 瀹夊叏鏂规硶璺宠繃
    cookie_token = request.cookies.get(settings.security.csrf_cookie, "")
    header_token = request.headers.get(settings.security.csrf_header, "")
    if not csrf_matches(cookie_token, header_token):
        auth_failures_total.labels(reason="csrf_mismatch").inc()
        raise Forbidden("CSRF 鏍￠獙澶辫触锛岃鍒锋柊椤甸潰鍚庨噸璇?)
```

| 鎯呭喌 | 鏄惁鏍￠獙 |
|---|---|
| API Key 璁よ瘉 | 鉂?璺宠繃锛堟祻瑙堝櫒涓嶄細鑷姩鎼哄甫锛屽ぉ鐒跺厤鐤級 |
| 浼氳瘽 + `GET`/`HEAD`/`OPTIONS`/`TRACE` | 鉂?璺宠繃 |
| 浼氳瘽 + `POST`/`PUT`/`PATCH`/`DELETE` | 鉁?**蹇呴』鏍￠獙** |

### 绗簩灞傦細鎺у埗鍙拌〃鍗曠殑闅愯棌瀛楁

HTML 琛ㄥ崟璧扮殑鏄?*鍙︿竴鏉?*鏍￠獙璺緞
锛坄console.py::_require_csrf`锛夛紝瀹冩帴鍙椾袱涓潵婧愶細

```python
def _require_csrf(request: Request, submitted: str) -> None:
    cookie = request.cookies.get(settings.security.csrf_cookie, "")
    header = request.headers.get(settings.security.csrf_header, "")
    provided = submitted or header                 # 琛ㄥ崟瀛楁浼樺厛锛屽叾娆¤姹傚ご
    if not cookie or not provided or not hmac.compare_digest(cookie, provided):
        log.warning("console_csrf_failed", path=request.url.path)
        raise Forbidden("椤甸潰宸茶繃鏈燂紝璇峰埛鏂板悗閲嶈瘯")
```

琛ㄥ崟閲屽繀椤诲甫闅愯棌瀛楁锛?
```html
<input type="hidden" name="csrf_token" value="{{ csrf_token }}">
```

**涓轰粈涔堝彔涓ゅ眰**锛氫細璇?Cookie 宸叉槸 `SameSite=Lax`锛堥樆鏂法绔?POST锛夛紝
鍙屾彁浜?Cookie 鍐嶅彔涓€灞傦紝鐢ㄤ簬闃插尽**鍚岀珯 XSS** 鍦烘櫙涓嬬殑瓒婃潈鎻愪氦銆?
### CSRF 浠ょ墝鐨勮疆鎹?
CSRF 浠ょ墝**鍙湪鐧诲綍/娉ㄥ唽鏃剁敓鎴愪竴娆?*锛屾暣涓細璇濇湡闂翠笉鍙樸€?娌℃湁瀹氭湡杞崲銆?
> 鈿狅笍 **褰撳墠缂哄彛**锛歚verify_csrf` 渚濊禆鐨勬槸
> `Depends(get_principal)`銆傛鏌ヤ竴涓嬫湁鍝簺**鏀瑰彉鐘舵€佺殑绔偣娌℃湁鎸?> `CurrentPrincipal`**锛屽氨涓嶄細缁忚繃 CSRF 鏍￠獙锛?>
> - `POST /webhooks/stripe` 鈥斺€?鏃犻壌鏉冿紝闈犵鍚嶄繚鎶わ紙**杩欐槸璁捐濡傛**锛?> - `POST /login` / `POST /signup` 鈥斺€?鏈櫥褰曠姸鎬侊紝鏃犱細璇濆彲淇濇姢
>   锛堥潬 `SameSite=Lax` + 闄愭祦闃叉姢锛?>
> 鍥犳**鏂板浠讳綍銆屼細璇濆彲璁块棶涓旀敼鍙樼姸鎬併€嶇殑绔偣鏃讹紝
> 蹇呴』纭瀹冮棿鎺ュ紩鐢ㄤ簡 `CurrentPrincipal`**锛堥€氳繃
> `WritablePrincipal` / `SessionPrincipal` / `AdminPrincipal`锛夈€?> 鐩存帴浣跨敤 `DbSession` + 鎵嬪啓閴存潈鐨勭鐐逛細缁曡繃 CSRF 鏍￠獙銆?
---

## 绉熸埛闅旂

**浠ｇ爜**锛歚services/projects.py`銆乣services/storage.py`銆?`security/deps.py::_resolve_org`銆乣api/routes/files.py`

杩欐槸 SaaS 鏈€甯歌鐨勫畨鍏ㄦ紡娲炴潵婧愶紝鍥犳鏈変笁閬撻槻绾裤€?
### 闃茬嚎 1锛氭煡璇㈠眰鐨勫己鍒?`org_id` 杩囨护

鏈嶅姟鍑芥暟鐨勭鍚嶆妸 `org_id` 鍋氭垚**蹇呭～鍏抽敭瀛楀弬鏁?*锛?璋冪敤鏂规棤娉曘€屽繕璁般€嶄紶锛?
```python
async def get_project(session: AsyncSession, *, org_id: str, project_id: str) -> Project:
    project = (await session.execute(
        select(Project).where(
            Project.id == project_id,
            Project.org_id == org_id,          # 鈫?鍐欐鍦ㄦ煡璇㈤噷
            Project.deleted_at.is_(None),
        )
    )).scalar_one_or_none()
    if project is None:
        raise NotFound("椤圭洰涓嶅瓨鍦?)
    return project
```

**娉ㄦ剰杩斿洖鐨勬槸 404 鑰屼笉鏄?403**鈥斺€旇繖鏄埢鎰忕殑锛?涓嶆硠婕忋€岃繖涓祫婧愭槸鍚﹀瓨鍦ㄣ€嶃€傛敾鍑昏€呮棤娉曢€氳繃 403/404 鐨勫樊寮?鏋氫妇鍑哄埆浜烘湁鍝簺 project_id銆?
**鏁版嵁妯″瀷鐨勬敮鎾?*锛氶櫎 `users` 澶栨墍鏈変笟鍔¤〃閮藉甫 `org_id`
锛坄db/models.py` 鐨勬ā鍧?docstring 鏄庣‘鍐欎簡杩欎竴鐐癸級銆?
### 闃茬嚎 2锛氱粍缁囧垏鎹㈠繀椤绘牎楠屾垚鍛樺叧绯?
```python
async def _resolve_org(session, user, request) -> tuple[Membership | None, Organization | None]:
    rows = (await session.execute(
        select(Membership, Organization)
        .join(Organization, Organization.id == Membership.org_id)
        .where(Membership.user_id == user.id)
        .order_by(Membership.created_at)
    )).all()
    if not rows:
        return None, None

    requested = request.headers.get("x-org-id", "").strip() if request else ""
    if requested:
        for membership, org in rows:
            if org.id == requested:
                return membership, org
        raise Forbidden("浣犱笉鏄缁勭粐鐨勬垚鍛?)     # 鈫?涓嶅湪鎴愬憳鍒楄〃閲屽氨鎷掔粷
    membership, org = rows[0]
    ...
```

鏀寔閫氳繃 `X-Org-Id` 澶村垏鎹㈢粍缁囷紝浣?*鍙湪鐢ㄦ埛鐨?membership 鍒楄〃閲屾煡鎵?*銆?涓嶅仛杩欎竴姝ュ氨鏄渶鍏稿瀷鐨勮秺鏉冩紡娲烇紙娉ㄩ噴鍘熻瘽锛夈€?
> 鈿狅笍 **涓€涓渶瑕佹敞鎰忕殑琛屼负宸紓**锛?> `is_suspended` 鐨勬鏌ュ彧鍦?*榛樿缁勭粐璺緞**锛坄rows[0]`锛変箣鍚庢墽琛屻€?> 鏄惧紡浼?`X-Org-Id` 鐨勫垎鏀?*涓嶆鏌?`is_suspended`**銆?>
> 鍥犳**鍋滅敤涓€涓粍缁囨椂锛屽繀椤诲悓鏃跺悐閿€瀹冪殑 API Key**锛?> 鍚﹀垯鎸佹湁鏈夋晥 Key 鐨勮皟鐢ㄦ柟浠嶈兘閫氳繃 `X-Org-Id` 澶磋闂?> 锛圓PI Key 璺緞鍊掓槸浼氬湪 `_principal_from_api_key` 閲屾鏌?`org.is_suspended`锛夈€?>
> 澶勭疆鏂瑰紡瑙?[OPERATIONS.md](docs/OPERATIONS.md) 绗?3.4 鑺傘€?
### 闃茬嚎 3锛氬瓨鍌ㄥ眰鐨?key 鍓嶇紑纭牎楠岋紙鏈€纭殑涓€閬擄級

**key 鐨勭粨鏋?*锛?
```
org/{org_id}/projects/{project_id}/{category}/{filename}
```

**绉熸埛淇℃伅鏄?key 鐨勭粨鏋勬€у墠缂€锛屼笉鏄檮甯︾殑鍏冩暟鎹?*鈥斺€?杩欒褰掑睘鏍￠獙鎴愪负涓€涓函瀛楃涓插墠缂€鍒ゆ柇锛圤(1)锛屼笉鏌ュ簱銆佷笉缂撳瓨銆?涓嶅瓨鍦ㄧ紦瀛樿繃鏈熷鑷寸殑瓒婃潈锛夈€?
```python
def key_belongs_to_org(key: str, org_id: str) -> bool:
    """绉熸埛闅旂鐨勭‖鏍￠獙銆備换浣曟寜 key 鐨勮鍙栭兘蹇呴』鍏堣繃杩欎竴鍏炽€?""
    normalized = key.replace("\\", "/").lstrip("/")      # 缁熶竴鏂滄潬銆佸幓鍓嶅鏂滄潬
    return normalized.startswith(f"org/{sanitize_filename(org_id, fallback='unknown')}/")
```

**鍦ㄨ矾鐢遍噷寮哄埗鎵ц**锛?
```python
@router.get("/files/{key:path}", include_in_schema=False)
async def get_file(key: str, request, settings, principal: CurrentPrincipal) -> Response:
    if not storage_svc.key_belongs_to_org(key, principal.org_id):
        log.warning("cross_tenant_file_access_blocked",
                    org_id=principal.org_id,
                    actor=principal.actor_label,
                    key=key[:120])
        raise Forbidden("鏃犳潈璁块棶璇ユ枃浠?)
```

**瓒婃潈灏濊瘯浼氳璁板綍鎴愮粨鏋勫寲鍛婅鏃ュ織**锛屽惈锛?
| 瀛楁 | 鍚箟 |
|---|---|
| `org_id` | 鏀诲嚮鑰呮墍灞炵粍缁?|
| `actor` | `user:<id>` 鎴?`api_key:reel_<prefix>_********` |
| `key` | 琚皾璇曡闂殑 key锛堟埅鏂埌 120 瀛楃锛?|

姝ｅ父鐢ㄦ埛涓嶄細瑙﹀彂杩欐潯鏃ュ織锛屽洜姝ゅ畠鏄竴涓?*楂樹环鍊肩殑瀹夊叏鐩戞帶淇″彿**銆?寤鸿瀵?`cross_tenant_file_access_blocked` 閰嶅憡璀︺€?
**闃茶矾寰勭┛瓒婄殑鍙岄噸淇濋櫓**锛坙ocal 鍚庣锛夛細

```python
def _path(self, key: str) -> Path:
    # 鍙岄噸淇濋櫓锛氭嫾鎺ュ悗鍐嶇‘璁や粛鍦ㄦ牴鐩綍鍐?    candidate = (self.root / key).resolve()
    if not str(candidate).startswith(str(self.root.resolve())):
        raise Forbidden("闈炴硶鐨勫瓨鍌ㄨ矾寰?)
    return candidate
```

**鏂囦欢鍚嶇殑娓呮礂**锛?
```python
_SAFE_SEGMENT = re.compile(r"[^A-Za-z0-9._\-]+")

def sanitize_filename(name: str, *, fallback: str = "file") -> str:
    base = Path(name or "").name                                # 鍓ユ帀鐩綍鎴愬垎
    cleaned = _SAFE_SEGMENT.sub("-", base).strip(".-")           # 鐧藉悕鍗曞寲 + 闃?. / ..
    if not cleaned:
        cleaned = fallback
    return cleaned[:120]
```

### 鍒嗕韩閾炬帴鐨勯殧绂?
鍒嗕韩**涓?*鎶?key 鍙樻垚鍏紑锛岃€屾槸绛惧彂涓€涓嫭绔?token锛?
- token 鏄?HMAC 绛惧悕锛坰alt `reelagent.share.v1`锛夛紝杞借嵎 `{"aid": <artifact_id>, "exp": <ts>}`銆?- 璁块棶 `/s/{token}` 鏃跺悓鏃舵牎楠岋細绛惧悕鏈夋晥 + 鏈繃鏈?+
  `artifact.is_public is True` + `artifact.deleted_at IS NULL`銆?- **鎾ら攢鍒嗕韩锛坄enable=false`锛変細璁╁凡鍙戝嚭鐨?URL 绔嬪嵆澶辨晥**鈥斺€?  鍥犱负鎺堟潈鐘舵€佸湪鏁版嵁搴擄紙`is_public`锛夎€屼笉鍦?URL 閲岋紝涓嶉渶瑕侀粦鍚嶅崟銆?- TTL **30 澶?*锛堢‖缂栫爜锛夈€?
### 褰撳墠涓嶆敮鎸佺殑闅旂鑳藉姏

- 鉂?**鎸夌鎴峰垎妗?/ 鐙珛瀛樺偍鍑瘉**锛堟墍鏈夌鎴峰叡鐢ㄤ竴涓?bucket锛?  闈?key 鍓嶇紑闅旂锛夈€傝繖鎰忓懗鐫€鏃犳硶鎸夌鎴峰仛瀛樺偍閰嶉锛?  涔熸棤娉曠粰澶у鎴蜂竴涓彧鑳借闂嚜宸卞墠缂€鐨?IAM policy銆?- 鉂?**琛岀骇瀹夊叏锛圥ostgreSQL RLS锛?*銆傞殧绂诲畬鍏ㄥ湪搴旂敤灞傚疄鐜般€?- 鉂?**绉熸埛绾у姞瀵嗗瘑閽?*锛堟墍鏈?BYOK 瀵嗛挜鐢ㄥ悓涓€鎶婄敱 `SECRET_KEY` 娲剧敓鐨勫瘑閽ュ姞瀵嗭級銆?- 鉂?**瀛樺偍灞傛娊璞＄殑鍐呯疆褰掑睘鏍￠獙**銆傚綋鍓嶄緷璧栥€屾瘡涓矾鐢遍兘璁板緱璋?  `key_belongs_to_org`銆嶁€斺€旇繖鏄?code review 灞傞潰鐨勭害鏉燂紝
  鑰屼笉鏄被鍨嬪眰闈㈢殑淇濊瘉銆?*杩欐槸褰撳墠璁捐鏈€鍊煎緱鏀硅繘鐨勫湴鏂?*
  锛堣 [ADR 0007](docs/adr/0007-tenant-scoped-artifact-storage-keys.md) 鐨勬柟妗?E锛夈€?
---

## 鍙ｄ护涓庡嚟璇?
**浠ｇ爜**锛歚security/passwords.py`銆乣security/tokens.py`

### 鍙ｄ护鍝堝笇锛欰rgon2id

```python
#: OWASP 鎺ㄨ崘妗ｏ細m=19MiB, t=2, p=1
_hasher = PasswordHasher(
    time_cost=2,
    memory_cost=19 * 1024,     # 19 MiB
    parallelism=1,
    hash_len=32,
    salt_len=16,
    type=Type.ID,              # Argon2id
)
```

鍙傛暟鍙栬嚜 OWASP 寤鸿鐨勩€岀害 50ms / 娆°€嶆。浣嶁€斺€斿畨鍏ㄤ笌鐧诲綍寤惰繜鐨勫钩琛＄偣銆?
**涓轰粈涔堢敤 Argon2id 鑰屼笉鏄?bcrypt/scrypt**锛?Argon2 鏄綋鍓嶅彛浠ゅ搱甯岀殑鎺ㄨ崘绠楁硶锛屽唴瀛樼‖锛坢emory-hard锛夌壒鎬?浣垮叾瀵?GPU/ASIC 鏆村姏鐮磋В鏈夋洿寮烘姷鎶楀姏銆?
### 鍙ｄ护寮哄害绛栫暐

`validate_password_strength()` 鐨勫叏閮ㄦ鏌ワ細

| 妫€鏌?| 闃堝€?| 澶辫触娑堟伅 |
|---|---|---|
| 鏈€灏忛暱搴?| `REEL_SECURITY__MIN_PASSWORD_LENGTH`锛堥粯璁?**10**锛?| `瀵嗙爜鑷冲皯闇€瑕?10 涓瓧绗 |
| 鏈€澶ч暱搴?| 256 | `瀵嗙爜杩囬暱锛堜笂闄?256 瀛楃锛塦 |
| 甯歌寮卞彛浠ら粦鍚嶅崟 | `_COMMON_PASSWORDS`锛?6 涓級 | `杩欎釜瀵嗙爜澶父瑙佷簡锛岃鎹竴涓猔 |
| 瀛楃绫诲埆 | 鈮?**3** 绫伙紙灏忓啓/澶у啓/鏁板瓧/绗﹀彿锛?| `瀵嗙爜闇€鑷冲皯鍖呭惈澶у啓瀛楁瘝銆佸皬鍐欏瓧姣嶃€佹暟瀛椼€佺鍙蜂腑鐨勪笁绫籤 |
| 涓嶅惈閭鍚?| 閭 @ 鍓嶉儴鍒嗭紙鈮? 瀛楃锛変笉鍑虹幇 | `瀵嗙爜涓嶈兘鍖呭惈浣犵殑閭鍚峘 |
| 瀛楃澶氭牱鎬?| 鍘婚噸鍚?鈮?5 涓笉鍚屽瓧绗?| `瀵嗙爜閲嶅瀛楃杩囧锛岃澧炲姞鍙樺寲` |

**榛戝悕鍗曠殑褰撳墠鍐呭**锛坄_COMMON_PASSWORDS`锛夛細

```
password, 12345678, 123456789, qwertyuiop, password1, admin123,
letmein123, welcome123, iloveyou, abc123456, 111111111, passw0rd,
reelagent, reelagent123, changeme, 1qaz2wsx3edc
```

> 鈿狅笍 **杩欎釜榛戝悕鍗曢潪甯稿皬**銆備唬鐮佹敞閲婂凡缁忚鏄庯細
> 銆屽父瑙佸急鍙ｄ护锛堢敓浜х幆澧冨簲鏇挎崲涓烘洿澶х殑瀛楀吀鎴栨帴鍏?HIBP锛夈€嶃€?> 鐢熶骇閮ㄧ讲寤鸿鎺ュ叆 [Have I Been Pwned 鐨?Pwned Passwords API](https://haveibeenpwned.com/API/v3#PwnedPasswords)
> 鍋?k-anonymity 鏌ヨ锛屾垨鏇挎崲涓烘洿澶х殑瀛楀吀锛堝 rockyou锛夈€?
**鍙ｄ护褰掍竴鍖?*锛氭牎楠屽墠鍏堝仛 `unicodedata.normalize("NFKC", password)`锛?鍥犳鍏ㄨ/鍗婅绛変环褰㈠紡琚粺涓€澶勭悊銆?
### 鍝堝笇鍙傛暟鐨勮嚜鍔ㄥ崌绾?
鐧诲綍鎴愬姛鍚庝細妫€鏌ユ棫鍝堝笇鏄惁闇€瑕佺敤鏂板弬鏁伴噸绠楋細

```python
if needs_rehash(user.password_hash):
    user.password_hash = hash_password(password)
    log.info("password_rehashed", user_id=user.id)
```

**`needs_rehash` 瀵规棤娉曡В鏋愮殑鍝堝笇杩斿洖 `True`**锛堜唬鐮佹敞閲婂師璇濓級锛?
> 鏃犳硶瑙ｆ瀽鐨勫搱甯屼竴寰嬭繑鍥?True锛氬畠鏄剧劧涓嶆槸褰撳墠鏍煎紡锛屽氨璇ラ噸绠椼€?> 鈥︹€︿絾璇箟涓婂繀椤绘槸 True 鈥斺€?杩斿洖 False 绛変簬銆屾部鐢ㄤ竴浠借涓嶆噦鐨勫搱甯屻€嶃€?
### API Key锛氶珮鐔电瀵嗙敤 SHA-256

```python
def hash_api_key(settings: Settings, full_key: str) -> str:
    return hashlib.sha256(f"{_pepper(settings)}{full_key}".encode()).hexdigest()
```

**涓轰粈涔堜笉鐢?Argon2**锛坄passwords.py` 鐨?docstring 鍘熻瘽锛夛細

> Argon2 鍙敤浜?*浣庣喌**绉樺瘑锛堜汉绫诲瘑鐮侊級銆?> API Key 鏄?256 浣嶉殢鏈轰覆锛岀敤甯?pepper 鐨?SHA-256 鍗冲彲锛?> 瀵瑰畠璺?Argon2 鍙細鐧界櫧鎷栨參姣忎釜 API 璇锋眰銆?
锛堟瘡涓?API 璇锋眰绾?50ms 鐨勫搱甯屽紑閿€鍦ㄧ敓浜т笂鏄笉鍙帴鍙楃殑銆傦級

**Key 鐨勭粨鏋?*锛?
```python
prefix = secrets.token_hex(4)                 # 8 浣嶅崄鍏繘鍒?secret = secrets.token_urlsafe(32)            # ~256 bit 鐔?full_key = f"reel_{prefix}_{secret}"
```

| 閮ㄥ垎 | 瀛樺偍鏂瑰紡 | 鐢ㄩ€?|
|---|---|---|
| `reel_` 鍥哄畾鍓嶇紑 | 鈥?| 渚夸簬鍦ㄤ唬鐮佷粨搴?鏃ュ織閲屽仛娉勬紡鎵弿 |
| `prefix`锛? 浣?hex锛?| **鏄庢枃**瀛?`api_keys.prefix` | 鍒楄〃灞曠ず銆佹棩蹇楀畾浣嶃€佸憡璀?|
| `secret` | 鍙繘 `key_hash = SHA-256(pepper + full_key)` | 璁よ瘉 |

**pepper 涓?`SECRET_KEY` 鍒嗙**锛屼究浜庣嫭绔嬭疆鎹細

| 閰嶇疆 | 璇存槑 |
|---|---|
| `REEL_SECURITY__API_KEY_PEPPER` | API Key 鐨勯澶?pepper |
| 闈炵敓浜х幆澧冪暀绌烘椂 | 鑷姩娲剧敓涓?`<secret_key>:apikey` |

> 鈿狅笍 **杞崲 `API_KEY_PEPPER` 浼氫娇鎵€鏈?API Key 绔嬪嵆澶辨晥**
> 锛堝搱甯屽涓嶄笂锛夈€傝繖鏄€屼竴閿悐閿€鎵€鏈?Key銆嶇殑鑳藉姏锛?> 浣嗕篃浼氭墦鏂墍鏈夐泦鎴愶紝闇€瑕佹彁鍓嶉€氱煡銆?
**鏁伴噺闄愬埗**锛氭瘡涓粍缁囨渶澶?**20** 涓椿璺?Key
锛坄revoked_at IS NULL` 鐨勮鏁帮級銆?**濂楅闄愬埗**锛氬厤璐圭増**涓嶅厑璁?*鍒涘缓 API Key銆?
### API Key 鐨勫悐閿€

- 鎾ら攢鏄?*杞挙閿€**锛歚revoked_at = now()`锛岃褰曚繚鐣欍€?- 鐢熸晥鍒ゅ畾锛歚is_active = revoked_at is None and not (expires_at and expires_at < now)`銆?- 鍙€夎繃鏈熸椂闂达細`expires_in_days`銆?
### 浣跨敤淇℃伅鐨勮褰曪紙鏈夎妭娴侊級

`api_keys` 璁板綍浜?`last_used_at` / `last_used_ip` / `request_count`锛?浣嗗啓鍏ユ湁 **60 绉掕妭娴?*锛?
```python
_LAST_USED_THROTTLE = timedelta(seconds=60)
...
if api_key.last_used_at is None or (now - api_key.last_used_at) > _LAST_USED_THROTTLE:
    await session.execute(update(ApiKey).where(...).values(...))
```

**杩欐槸涓€涓湁鎰忕殑鍙栬垗**锛氶伩鍏嶆瘡涓?API 璇锋眰閮戒骇鐢熶竴娆?UPDATE
锛堜細鏄捐憲鏀惧ぇ鏁版嵁搴撳啓鍏ュ帇鍔涳級銆?
**瀹夊叏褰卞搷**锛歚last_used_at` **涓嶈兘**鐢ㄤ簬绮剧‘鐨勫彇璇佹椂闂寸嚎銆?涓€娆℃硠婕忕殑 Key 鍦ㄨ浣跨敤鍚庣殑 60 绉掑唴锛宍request_count` 涔熶笉浼氭洿鏂般€?闇€瑕佺簿纭璁℃椂搴斾緷璧?`audit_logs` 涓庤闂棩蹇椼€?
### 鍒嗕韩 token

- HMAC 绛惧悕锛坰alt `reelagent.share.v1`锛夛紝杞借嵎 `{"aid", "exp"}`銆?- **娌℃湁 `max_age` 闄愬埗**锛岃繃鏈熷畬鍏ㄩ潬杞借嵎閲岀殑 `exp` 瀛楁妫€鏌ャ€?- TTL 30 澶┿€?- **token 鏈韩涓嶅尯鍒嗙鎴?*鈥斺€旈殧绂婚潬 `artifact_id` + `is_public` 鏍￠獙銆?
---

## 瀵嗛挜鍔犲瘑涓庤劚鏁?
**浠ｇ爜**锛歚security/tokens.py`銆乣config.py`銆?`observability/logging.py`

### 涓夊眰瀵嗛挜鐨勫鐞嗘柟寮?
| 瀵嗛挜绫诲瀷 | 澶勭悊鏂瑰紡 | 鑳藉惁鍙栧洖鏄庢枃 |
|---|---|---|
| **骞冲彴渚涘簲鍟?Key**锛坄REEL_PROVIDERS__*__API_KEY`锛?| pydantic `SecretStr`锛岃繘绋嬪唴瀛?| 鉁?鏈嶅姟绔敤锛堜笉鍦ㄥ簱閲岋級 |
| **鐢ㄦ埛 BYOK 渚涘簲鍟?Key**锛坄organizations.byok`锛?| **Fernet 瀵圭О鍔犲瘑**鍚庡瓨搴?| 鉁?鏈嶅姟绔敤锛堥渶 `SECRET_KEY`锛?|
| **鐢ㄦ埛鍙ｄ护** | Argon2id 鍗曞悜鍝堝笇 | 鉂?|
| **API Key** | `SHA-256(pepper + key)` 鍗曞悜鍝堝笇 | 鉂岋紙鏄庢枃鍙湪鍒涘缓鏃惰繑鍥炰竴娆★級 |
| **浼氳瘽 / 鍒嗕韩 token** | HMAC 绛惧悕锛堜笉鍚瀵嗭級 | 鈥?|

### BYOK 瀵嗛挜锛氫负浠€涔堝繀椤诲彲閫?
鐢ㄦ埛鑷甫鐨勪緵搴斿晢 Key **蹇呴』鑳借瑙ｅ瘑**鈥斺€旀湇鍔＄瑕佺敤瀹冨幓璋冨巶鍟嗙殑 API銆?鍥犳涓嶈兘鐢ㄥ搱甯岋紝鍙兘鐢ㄥ绉板姞瀵嗭細

```python
def _fernet(settings: Settings) -> Fernet:
    raw = settings.security.secret_key.get_secret_value().encode()
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(raw).digest()))
```

**鍔犲瘑瀵嗛挜鐢?`REEL_SECURITY__SECRET_KEY` 缁?SHA-256 娲剧敓銆?*

### BYOK 瀵嗛挜鐨勪娇鐢ㄨ竟鐣?
**浠讳綍鎺ュ彛閮戒笉浼氭槑鏂囪繑鍥?BYOK 瀵嗛挜銆?* 璁剧疆椤靛彧鏄剧ず銆屽凡閰嶇疆/鏈厤缃€嶏細

```python
byok_plain = decrypt_byok_map(settings, state["org"].byok or {})
...
"byok_status": {k: bool(v) for k, v in byok_plain.items()},
```

**鍗曟潯瑙ｄ笉寮€涓嶅奖鍝嶅叾瀹冧緵搴斿晢**锛?
```python
def decrypt_byok_map(settings, values) -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in (values or {}).items():
        try:
            out[k] = decrypt_secret(settings, v)
        except TokenError:
            continue          # 鍗曟潯瑙ｄ笉寮€涓嶅奖鍝嶅叾瀹冧緵搴斿晢
    return out
```

> 鈿狅笍 **杩欎釜瀹归敊鐨勫壇浣滅敤**锛氬鏋?`SECRET_KEY` 琚疆鎹紝
> 鎵€鏈?BYOK 瀵嗛挜閮借В涓嶅紑锛屼絾**涓嶄細鎶ラ敊**鈥斺€?> `resolve_engine_mode()` 浼氳涓恒€屾病鏈夎棰?Key銆嶏紝
> 浜庢槸锛堢敓浜х幆澧冿級鎷掔粷鎵ц鐢熸垚璇锋眰锛屾垨锛堥潪鐢熶骇鐜锛夐€€鍒?mock銆?> 鐢ㄦ埛鐪嬪埌鐨勭棁鐘舵槸銆岀敓鎴愪笉浜嗙墖瀛愩€嶏紝鑰屼笉鏄€屽瘑閽ヨВ瀵嗗け璐ャ€嶃€?> 鎺掗殰鏃堕渶瑕佹鏌ユ棩蹇楅噷鏈夋病鏈?`瀵嗛挜瑙ｅ瘑澶辫触锛汼ECRET_KEY 鍙兘宸茶疆鎹
> 锛堝畠鍙湪**鍗曟潯**瑙ｅ瘑澶辫触鏃剁敱 `decrypt_secret` 鎶涘嚭锛?> 琚?`decrypt_byok_map` 鍚炴帀锛夈€?
### `SECRET_KEY` 鐨勮疆鎹㈠悗鏋?
`REEL_SECURITY__SECRET_KEY` 鏄竴涓?*娲剧敓鏍?*锛岃鍥涗欢浜嬩娇鐢細

| 鐢ㄩ€?| 杞崲鍚庣殑鍚庢灉 |
|---|---|
| 浼氳瘽绛惧悕锛坰alt `reelagent.session.v1`锛?| 鉁?鎵€鏈変細璇濈珛鍗冲け鏁堬紙鐢ㄦ埛閲嶆柊鐧诲綍锛?|
| 鍒嗕韩 token 绛惧悕锛坰alt `reelagent.share.v1`锛?| 鉁?鎵€鏈夊垎浜摼鎺ュけ鏁?|
| BYOK 鍔犲瘑锛團ernet锛?| 鉂?**鎵€鏈?BYOK 瀵嗛挜鏃犳硶瑙ｅ瘑**锛岀敤鎴峰繀椤婚噸鏂板～鍐?|
| API Key pepper 鐨勯粯璁ゆ淳鐢熷€硷紙闈炵敓浜х幆澧冿級 | 鈿狅笍 浠呭綋 `API_KEY_PEPPER` 鏈厤缃椂褰卞搷锛涢厤缃簡灏辨棤褰卞搷 |

**鍥犳 `SECRET_KEY` 搴斿綋浣滀负闀挎湡绋冲畾鐨勫瘑閽ョ鐞嗭紝鑰屼笉鏄畾鏈熻疆鎹㈢殑绉樺瘑銆?*
濡傛灉纭疄闇€瑕佽疆鎹紝闇€瑕佷竴濂楀弻瀵嗛挜杩囨浮鏂规鈥斺€?*褰撳墠鏈疄鐜?*銆?
### SecretStr 鎵撶爜

鎵€鏈?Secret 瀛楁鐢?pydantic 鐨?`SecretStr`锛?
```python
class SecuritySettings(BaseModel):
    secret_key: SecretStr = SecretStr("")
    api_key_pepper: SecretStr = SecretStr("")
```

`repr()` 浼氭樉绀?`SecretStr('**********')`锛屾棤娉曚粠鏃ュ織鎴栧紓甯镐俊鎭噷娉勬紡銆?
**鍙畨鍏ㄥ啓鏃ュ織鐨勯厤缃揩鐓?*锛?
```python
def safe_dump(self) -> dict[str, object]:
    """鍙畨鍏ㄥ啓鍏ユ棩蹇?鐘舵€侀〉鐨勯厤缃揩鐓э紙鎵€鏈夊瘑閽ュ凡鎵撶爜锛夈€?""
    return {
        "env": self.env.value,
        "base_url": self.base_url,
        "database": "sqlite" if self.database.is_sqlite else "postgresql",
        "redis": self.redis.enabled,
        "storage": self.storage.backend,
        "billing": self.billing.enabled,
        "rate_limit": self.rate_limit.enabled,
        "providers": {
            "llm": {"provider": ..., "configured": bool},     # 鍙毚闇叉槸鍚﹂厤缃?            ...
        },
    }
```

**娉ㄦ剰瀹冨彧鏆撮湶 `configured: bool`锛屼笉鏆撮湶 Key 鏈韩锛屼篃涓嶆毚闇叉ā鍨嬩箣澶栫殑鍙傛暟銆?*

### 鏃ュ織鑴辨晱锛堝鐞嗗櫒灞傞潰锛?
**杩欐槸鏈€閲嶈鐨勪竴灞?*锛氫笉渚濊禆姣忎釜璋冪敤鐐硅嚜瑙夛紝鑰屾槸鍦?structlog
澶勭悊鍣ㄩ摼閲屽仛姝ｅ垯鎿﹂櫎銆?
**鍙岄噸鍖归厤**锛?
```python
_SECRET_KEY_PATTERN = re.compile(
    r"(api[_-]?key|apikey|secret|password|passwd|token|authorization|bearer|"
    r"client[_-]?secret|webhook[_-]?secret|session|cookie|pepper|dsn)",
    re.IGNORECASE,
)
_SECRET_VALUE_PATTERN = re.compile(
    r"\b(sk-[A-Za-z0-9_\-]{8,}|Bearer\s+[A-Za-z0-9._\-]{8,}|"
    r"whsec_[A-Za-z0-9]{8,}|AKIA[0-9A-Z]{12,})\b"
)
REDACTED = "***REDACTED***"
```

| 瑙﹀彂鏉′欢 | 鍔ㄤ綔 |
|---|---|
| **瀛楀吀閿悕**鍖归厤 `_SECRET_KEY_PATTERN` | 鍊兼浛鎹负 `***REDACTED***` |
| **瀛楃涓插€?*鍖归厤 `_SECRET_VALUE_PATTERN`锛坄sk-` / `Bearer ` / `whsec_` / `AKIA`锛?| 鍖归厤閮ㄥ垎鏇挎崲涓?`***REDACTED***` |
| 閫掑綊娣卞害 > 4 | 鍋滄閫掑綊锛堟€ц兘淇濇姢锛?|

**瑕嗙洊绗笁鏂瑰簱鐨勬棩蹇?*锛?
```python
formatter = structlog.stdlib.ProcessorFormatter(
    foreign_pre_chain=shared,          # 鈫?鍏抽敭锛氬閮?logger 涔熻繃鑴辨晱閾?    processors=[...],
)
```

> 涓轰粈涔堜笉鐢?`PrintLoggerFactory`锛堜唬鐮佹敞閲婂師璇濓級锛?> 銆岄偅鏍?uvicorn / SQLAlchemy / httpx 鐨勬棩蹇椾細缁曡繃鎴戜滑鐨勫鐞嗗櫒锛?> 鏃笉缁熶竴鏍煎紡涔?*涓嶅仛瀵嗛挜鑴辨晱**銆傘€?
**鍣簱闄嶅櫔**锛坄WARNING` 浠ヤ笂鎵嶈緭鍑猴級锛?`uvicorn.access` / `uvicorn.error` / `sqlalchemy.engine.Engine` /
`sqlalchemy.pool` / `httpx` / `httpcore` / `aiosqlite` / `asyncio`銆?
> 鈩癸笍 娉ㄦ剰 `uvicorn.access` 琚檷鍒?`WARNING`锛屼笖
> `uvicorn.run(..., access_log=False)`鈥斺€?*搴旂敤涓嶄娇鐢?uvicorn 鐨勮闂棩蹇?*锛?> 鑰屾槸鐢ㄨ嚜宸辩殑缁撴瀯鍖栬闂棩蹇楋紙甯?`request_id`銆乣route`銆佽€楁椂銆佽劚鏁忥級銆?
### 褰撳墠涓嶆敮鎸佺殑瀵嗛挜绠＄悊鑳藉姏

- 鉂?**KMS / Vault 闆嗘垚**锛堝瘑閽ヤ粠鐜鍙橀噺璇诲彇锛屼笉涓诲姩浠庡瘑閽ョ鐞嗙郴缁熷彇锛?- 鉂?**鎸夌鎴风殑鍔犲瘑瀵嗛挜**锛堟墍鏈?BYOK 鐢ㄥ悓涓€鎶婃淳鐢熷瘑閽ワ級
- 鉂?**瀵嗛挜杞崲鐨勫弻瀵嗛挜杩囨浮**
- 鉂?**瀵嗛挜璁块棶瀹¤**锛堟棤娉曠煡閬撳摢涓瘑閽ュ湪浠€涔堟椂鍊欒瑙ｅ瘑杩囷級

---

## HTTP 瀹夊叏鍝嶅簲澶?
**浠ｇ爜**锛歚api/middleware.py::SecurityHeadersMiddleware`

| 鍝嶅簲澶?| 鍊?| 鏉′欢 |
|---|---|---|
| `X-Content-Type-Options` | `nosniff` | 鎬绘槸 |
| `X-Frame-Options` | `DENY` | 鎬绘槸 |
| `Referrer-Policy` | `strict-origin-when-cross-origin` | 鎬绘槸 |
| `Permissions-Policy` | `camera=(), microphone=(), geolocation=(), interest-cohort=()` | 鎬绘槸 |
| `Cross-Origin-Opener-Policy` | `same-origin` | 鎬绘槸 |
| `Cross-Origin-Resource-Policy` | `cross-origin` | 浠?`/files/` 涓?`/s/` |
| `Cross-Origin-Resource-Policy` | `same-origin` | 鍏跺畠璺緞 |
| `Strict-Transport-Security` | `max-age=31536000; includeSubDomains` | **浠?`https_only=true`** |
| `Content-Security-Policy` | 瑙佷笅 | 鎬绘槸锛堥櫎闈炰笂娓稿凡璁剧疆锛?|
| `X-Request-ID` | 璇锋眰 ID | 鎬绘槸 |
| `Server-Timing` | `app;dur=<ms>` | 鎬绘槸 |

**涓轰粈涔?`/files/` 涓?`/s/` 鏀捐 `cross-origin`**锛?鎴愮墖闇€瑕佽兘琚祵鍏ュ埌椤甸潰锛堟垨绗笁鏂圭珯鐐癸級閲屽睍绀恒€?浠ｇ爜娉ㄩ噴鍘熻瘽锛氥€屼笂浼犵殑濯掍綋璧勬簮鍏佽璺ㄦ簮灞曠ず锛堟垚鐗囪宓屽湪椤甸潰閲岋級锛屽叾浣欎竴寰嬪悓婧愩€嶃€?
### Content-Security-Policy

```
default-src 'self'
script-src 'self'
style-src 'self' 'unsafe-inline'
img-src 'self' data: blob:
media-src 'self' blob:
font-src 'self' data:
connect-src 'self'
frame-ancestors 'none'
base-uri 'self'
form-action 'self'
object-src 'none'
```

| 鎸囦护 | 瀹夊叏鍚箟 |
|---|---|
| `script-src 'self'` | **涓嶅惈 `'unsafe-inline'`**鈥斺€旀墍鏈夎剼鏈繀椤绘槸澶栭儴鏂囦欢锛岃繖鏄 XSS 鏈€寮虹殑涓€閬撻槻绾?|
| `style-src 'self' 'unsafe-inline'` | 鏍峰紡鍗曠嫭鏀捐鍐呰仈锛堣繘搴︽潯瀹藉害绛夊満鏅級 |
| `frame-ancestors 'none'` | 涓嶈兘琚换浣曠珯鐐瑰祵鍏ワ紙鐐瑰嚮鍔寔闃叉姢锛屼笌 `X-Frame-Options: DENY` 鍙屼繚闄╋級 |
| `base-uri 'self'` | 闃叉 `<base>` 鏍囩鍔寔鐩稿 URL |
| `form-action 'self'` | 琛ㄥ崟鍙兘鎻愪氦鍒板悓婧愶紙闃叉琛ㄥ崟琚姭鎸佸埌澶栭儴锛?|
| `object-src 'none'` | 绂佹鎻掍欢锛團lash/Java 绛夊巻鍙叉敾鍑婚潰锛?|
| `img-src ... blob:` / `media-src ... blob:` | 鍏佽 `URL.createObjectURL()` 鐢熸垚鐨?blob锛堥瑙堢敤锛?|

**闈炵敓浜х幆澧?*棰濆鏀捐 `connect-src` 鐨?`ws:` 涓?`wss:`
锛堢敤浜庤繛鎺ユ湰鍦?API 鏂囨。鐨?websocket锛夈€?
**鍚敤 Stripe 鏃?*锛坄billing.enabled=true`锛夐澶栬拷鍔狅細

```
connect-src 'self' https://api.stripe.com
frame-src https://js.stripe.com https://checkout.stripe.com
script-src 'self' https://js.stripe.com        鈫?娉ㄦ剰锛氳鐩栦簡鍘熸潵鐨?script-src
```

> 鈿狅笍 **鍗充娇鍚敤 Stripe锛宍script-src` 涔熶粛鐒朵笉鍚?`'unsafe-inline'`**銆?> 杩欐槸鍒绘剰鐨勭害鏉燂紙浠ｇ爜娉ㄩ噴鍘熻瘽锛夛細
> 銆孋SP 榛樿鎷掔粷鍐呰仈鑴氭湰锛屽洜姝ゆā鏉块噷鎵€鏈?JS 閮藉繀椤昏蛋澶栭儴鏂囦欢鈥斺€?> 杩欐潯绾︽潫涓€寮€濮嬪氨瀹氭锛岄伩鍏嶅悗鏈熶负浜嗐€庡厛璺戣捣鏉ャ€忔斁瀹?CSP 鍐嶄篃鏀朵笉鍥炴潵銆傘€?
**涓婃父宸茶缃椂涓嶈鐩?*锛?
```python
if not response.headers.get("Content-Security-Policy"):
    response.headers["Content-Security-Policy"] = _csp(settings)
```

鍥犳浣犲彲浠ュ湪鍙嶅悜浠ｇ悊灞傝缃洿涓ユ牸鐨?CSP銆?
### `TrustedHostMiddleware`

```python
hosts = settings.security.allowed_hosts
if hosts and "*" not in hosts:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=hosts)
```

**鍙湁 `allowed_hosts` 涓嶅惈 `*` 鏃舵墠鍚敤銆?*
鐢熶骇鐜蹇呴』鏄惧紡閰嶇疆锛堝惎鍔ㄦ牎楠屼細寮哄埗锛夛紝鍥犳鐢熶骇鐜涓嬭繖涓腑闂翠欢鎬绘槸鍚敤鐨勩€?
**浣滅敤**锛氭嫆缁?`Host` 澶翠笉鍖归厤鐨勮姹傦紝
闃叉 Host 澶存敞鍏ュ鑷寸殑瀵嗙爜閲嶇疆閾炬帴鎶曟瘨銆佺紦瀛樻姇姣掔瓑闂銆?
### 褰撳墠涓嶆敮鎸佺殑鑳藉姏

- 鉂?**`X-XSS-Protection`**锛堢幇浠ｆ祻瑙堝櫒宸插純鐢ㄨ澶达紝涓嶉渶瑕佽缃紱
  濡傛灉浣犵殑鍚堣鎵弿瑕佹眰瀹冿紝寤鸿璁?`0` 鑰屼笉鏄?`1`锛?- 鉂?**Certificate Transparency 涓婃姤**
- 鉂?**Signed Exchanges / SRI锛堝瓙璧勬簮瀹屾暣鎬э級**銆?  鍥犱负 CSP 宸茬姝㈠閮ㄨ剼鏈紝SRI 鐨勬剰涔夋湁闄愩€?- 鉂?**涓婃姤绔偣锛圕SP `report-uri`锛?*

---

## 闄愭祦

**浠ｇ爜**锛歚api/ratelimit.py`銆乣config.py::RateLimitSettings`

### 涓ょ鍚庣

| 鍚庣 | 绠楁硶 | 閫傜敤鍦烘櫙 | 鐘舵€佸叡浜?|
|---|---|---|---|
| **Redis** | 鍥哄畾绐楀彛璁℃暟鍣紙`INCR` + `EXPIRE NX`锛?| 鐢熶骇鎺ㄨ崘 | 鉁?澶氬疄渚嬪叡浜?|
| **杩涚▼鍐?* | 婊戝姩绐楀彛 deque + 鎯版€ф竻鐞嗭紙姣?5 鍒嗛挓鎵竴娆★級 | 鍗曞疄渚嬪鐢?| 鉂?鍚勮繘绋嬬嫭绔?|

```python
if settings.rate_limit.enabled and settings.redis.enabled:
    try:
        self._redis = aioredis.from_url(...)
    except Exception as exc:
        log.warning("redis_unavailable_falling_back", error=str(exc))
        self._redis = None
```

**Redis 杩炴帴澶辫触浼氳嚜鍔ㄩ€€鍖栦负杩涚▼鍐呭疄鐜?*锛堟墦 `redis_unavailable_falling_back` 璀﹀憡锛夈€?
> 鈿狅笍 **澶氬壇鏈?+ 杩涚▼鍐呴檺娴?= 瀹為檯闄愰 脳 鍓湰鏁?*銆?> 鐢熶骇澶氬壇鏈儴缃?*蹇呴』**鍚敤 Redis銆?
### fail-open 绛栫暐锛堜竴涓湁浜夎鐨勫喅瀹氾級

```python
try:
    count = await self._incr(key, limit.window_seconds)
except Exception as exc:
    # fail-open锛氶檺娴佸櫒鏁呴殰涓嶈兘鎷栧灝绔欑偣
    log.warning("rate_limit_backend_error", scope=limit.scope, error=str(exc))
    return
```

**闄愭祦鍣ㄥ悗绔晠闅滄椂鏀捐锛岃€屼笉鏄嫆缁濄€?*

浠ｇ爜娉ㄩ噴鏄庣‘鍐欎簡杩欎釜鍙栬垗鐨勭悊鐢憋紝鍥犱负瀹冩槸鏈変簤璁殑锛?
| 瑙嗚 | 璁烘嵁 |
|---|---|
| **鏀寔 fail-open锛堝綋鍓嶉€夋嫨锛?* | 闄愭祦鍣ㄦ槸**鍙敤鎬т繚鎶?*鎵嬫锛屼笉鏄畨鍏ㄨ竟鐣屻€傚畠鎸備簡涓嶅簲璇ヨ鏁翠釜绔欑偣涓嶅彲鐢ㄣ€傚彲鐢ㄦ€т紭鍏堛€?|
| **鍙嶅锛坒ail-closed 鐨勭悊鐢憋級** | 闄愭祦澶辨晥鏃讹紝鎾炲簱銆佸埛棰濆害銆丏oS 閮藉け鍘讳簡闃叉姢銆傚銆岄搴︺€嶈繖绉嶇洿鎺ュ搴旈挶鐨勮祫婧愶紝fail-open 鎰忓懗鐫€鍙互琚棤闄愬埛銆?|

**褰撳墠閫夋嫨 fail-open銆?* 濡傛灉浣犵殑濞佽儊妯″瀷鏇寸湅閲嶉搴︿繚鎶わ紝
鍙互閫氳繃缁?`project_create` 涓?`login` 鍔?*鐙珛浜庨檺娴佸櫒鐨?*涓氬姟绾у畧鍗潵缂撹В
锛堝綋鍓嶆湭瀹炵幇锛夈€?
### 闄愰琛?
| scope | 渚濇嵁 | 闄愰 | 绐楀彛 | 閰嶇疆椤?|
|---|---|---|---|---|
| `anonymous` | 瀹㈡埛绔?IP | 30 | 60s | `REEL_RATE_LIMIT__ANONYMOUS_PER_MINUTE` |
| `user` | 鐢ㄦ埛 ID | 240 | 60s | `REEL_RATE_LIMIT__USER_PER_MINUTE` |
| `api_key` | API Key ID | 300 | 60s | `REEL_RATE_LIMIT__API_KEY_PER_MINUTE` |
| `login` | 瀹㈡埛绔?IP | 10 | 300s | `REEL_SECURITY__LOGIN_MAX_ATTEMPTS` / `LOGIN_WINDOW_SECONDS` |
| `signup` | 瀹㈡埛绔?IP | 5 | 3600s | 鍥哄畾鍊?|
| `project_create` | **缁勭粐 ID** | 60 | 3600s | `REEL_RATE_LIMIT__PROJECT_CREATES_PER_HOUR` |
| `upload` | 鈥?| 30 | 3600s | 鍥哄畾鍊硷紙**褰撳墠鏃犺皟鐢ㄦ柟**锛?|

**瀹為檯琚鏌ョ殑绔偣**锛?
| 绔偣 | scope |
|---|---|
| `POST /login` | `login` |
| `POST /signup` | `signup` |
| `POST /api/v1/projects` | `project_create`锛堟寜 org锛? `api_key`锛堝鏋滄槸 API Key 韬唤锛?|

> 鈿狅笍 **`anonymous` 涓?`user` 涓や釜 scope 鍦ㄤ唬鐮侀噷瀹氫箟浜嗕絾娌℃湁璋冪敤鐐广€?*
> 涔熷氨鏄锛岄櫎浜嗕笂闈笁涓鐐癸紝**鍏跺畠鎺ュ彛鐩墠涓嶅彈闄愭祦淇濇姢**銆?> 杩欏寘鎷?`GET /api/v1/projects`锛堝垪琛ㄦ煡璇級銆佹墍鏈?`GET` 绔偣绛夈€?>
> 杩欐槸涓€涓渶瑕佽ˉ榻愮殑鍦版柟鈥斺€斿挨鍏舵槸鍒楄〃绫荤鐐癸紝鏈彈淇濇姢鐨?> 楂橀鏌ヨ鍙兘閫犳垚鏁版嵁搴撳帇鍔涖€?
### 瀹㈡埛绔?IP 鐨勫垽瀹?
```python
def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        first = forwarded.split(",")[0].strip()
        if first and _is_private(first):        # 绗竴璺虫槸鍐呯綉/鍥炵幆 鈫?鍙俊浠ｇ悊閾?            parts = [p.strip() for p in forwarded.split(",") if p.strip()]
            return parts[-1] if parts else first
        return first
    real_ip = request.headers.get("x-real-ip", "").strip()
    if real_ip:
        return real_ip
    return request.client.host if request.client else ""
```

**璁捐鎰忓浘**锛氬彧鍦?*鏄庣‘淇′换**鍙嶅悜浠ｇ悊鏃舵墠浣跨敤 `X-Forwarded-For`鈥斺€?鍚﹀垯鏀诲嚮鑰呭彲浠ヤ吉閫犺澶寸粫杩囧熀浜?IP 鐨勯檺娴併€?
**浣嗗疄鐜版湁涓€涓竟鐣?*锛氬綋 `XFF` 鐨勭涓€璺虫槸**鍏綉 IP** 鏃讹紝
浠ｇ爜鐩存帴杩斿洖瀹冿紙`return first`锛夆€斺€旇繖鎰忓懗鐫€
**濡傛灉璇锋眰鏈粡浠ｇ悊鐩存帴鍒拌揪搴旂敤锛屾敾鍑昏€呭彲浠ヤ吉閫犱换鎰?`XFF` 鍊?*銆?
> 鈿狅笍 **杩欐槸蹇呴』闈犻儴缃蹭晶闃叉姢鐨勭偣**锛?> **搴旂敤鏃犳硶鍖哄垎銆屽彲淇′唬鐞嗗姞鐨?XFF銆嶄笌銆屾敾鍑昏€呬吉閫犵殑 XFF銆嶃€?*
> 鍥犳蹇呴』纭繚搴旂敤绔彛涓嶅鍏綉寮€鏀撅紙鍙鍙嶅悜浠ｇ悊鑳借闂級銆?> 鍚﹀垯 `anonymous` / `login` / `signup` 涓変釜鍩轰簬 IP 鐨勯檺娴佸叏閮ㄥ彲琚粫杩囥€?> 璇﹁ [DEPLOYMENT.md](docs/DEPLOYMENT.md) 绗?4.4 鑺傘€?
### 闄愭祦鐨勫搷搴?
```json
{
  "error": {
    "code": "rate_limited",
    "message": "鎿嶄綔杩囦簬棰戠箒锛岃 60 绉掑悗鍐嶈瘯",
    "details": {"scope": "project_create", "retry_after_seconds": 60}
  },
  "request_id": "..."
}
```

HTTP 429锛屽苟甯?`Retry-After: 60` 鍝嶅簲澶淬€?
鎸囨爣锛歚reelagent_rate_limit_hits_total{scope="..."}`銆?
---

## Webhook 瀹夊叏

**浠ｇ爜**锛歚api/routes/billing.py`銆乣services/billing.py`

### 涓夋潯纭€ц姹?
#### 鈶?蹇呴』鐢ㄥ師濮嬭姹備綋楠岀

```python
@router.post("/stripe")
async def stripe_webhook(request: Request, settings: AppSettings,
                         stripe_signature: str = Header("", alias="Stripe-Signature")) -> Response:
    payload = await request.body()          # 鈫?鍘熷瀛楄妭锛屼笉缁忚繃浠讳綍瑙ｆ瀽
```

**璺敱涓嶆寕浠讳綍 body 瑙ｆ瀽渚濊禆**锛堟病鏈?pydantic 妯″瀷鍙傛暟锛夈€?浠ｇ爜娉ㄩ噴瑙ｉ噴浜嗗師鍥狅細

> 銆屼换浣曚腑闂翠欢鑻ユ彁鍓嶆秷璐?鏀瑰啓 body锛堜緥濡傛妸 JSON 瑙ｆ瀽涓€閬嶏級锛岀鍚嶅氨浼氬涓嶄笂銆傘€?
**閮ㄧ讲瑕佹眰**锛氬弽鍚戜唬鐞嗕笉鑳芥敼鍐欒姹備綋銆傝瑙?[DEPLOYMENT.md](docs/DEPLOYMENT.md) 绗?6.3 鑺傘€?
#### 鈶?蹇呴』鍏堣惤搴撳啀澶勭悊锛堝箓绛夛級

```python
row, first = await record_event(session, provider="stripe", event=event)
if not first:
    return "duplicate"
```

`webhook_events(provider, event_id)` 鏈?*鍞竴绾︽潫**銆?鍏堟彃鍏ュ啀澶勭悊锛屽洜姝ら噸澶嶆姇閫掍細琚尅涓嬨€?
`event_id` 缂哄け鐩存帴鎷掔粷锛?
```python
if not event_id:
    raise ValidationFailed("Webhook 缂哄皯浜嬩欢 ID")
```

#### 鈶?澶勭悊澶辫触蹇呴』杩斿洖 5xx

```python
except Exception as exc:
    row.status = WebhookStatus.FAILED
    row.error = f"{type(exc).__name__}: {exc}"[:1000]
    log.exception("webhook_failed", event_type=event_type, error=str(exc))
    raise
```

璺敱灞傛妸瀹冪炕璇戞垚 500锛?
```python
except Exception as exc:
    log.exception("stripe_webhook_processing_failed", event_id=event_id, error=str(exc))
    return JSONResponse({"error": "processing_failed", "event_id": event_id},
                        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)
```

> 銆屼笉鍚炲紓甯革細杩斿洖 5xx 璁?Stripe 閲嶈瘯銆傘€?> 銆屽悶鎺夊紓甯歌繑鍥?200 浼氬鑷寸敤鎴蜂粯浜嗛挶娌″埌璐︺€傘€?
### 绛惧悕楠岃瘉

鐢?Stripe 瀹樻柟 SDK锛?
```python
def verify_and_parse(settings: Settings, payload: bytes, signature: str) -> dict[str, Any]:
    stripe = _stripe(settings)
    secret = settings.billing.webhook_secret.get_secret_value()
    if not secret:
        raise UnsupportedOperation("鏈厤缃?Stripe webhook secret")
    try:
        event = stripe.Webhook.construct_event(payload, signature, secret)
    except Exception as exc:
        raise ValidationFailed(f"Webhook 绛惧悕鏍￠獙澶辫触锛歿exc}") from exc
    return dict(event)
```

| 鎯呭喌 | 缁撴灉 |
|---|---|
| 鏈厤缃?`WEBHOOK_SECRET` | `unsupported_operation`锛?00锛?|
| 绛惧悕鏃犳晥 | `validation_failed`锛?00锛夛紝鏃ュ織 `stripe_webhook_verify_failed` |
| `billing.enabled = false` | 200 + `{"ignored": "billing disabled"}` |
| 璇锋眰浣撲负绌?| 400 + `{"error": "empty payload"}` |

> 鈿狅笍 **Webhook 绛惧悕澶辫触鐨勮璁″惈涔?*锛氶獙绛惧け璐ヨ繑鍥?**400**锛堜笉鏄?5xx锛夛紝
> 鍥犳 **Stripe 涓嶄細閲嶈瘯**銆傝繖鏄鐨勨€斺€斿鏋滄槸浼€犺姹傦紝
> 閲嶈瘯涔熸病鎰忎箟锛涘鏋滄槸閰嶇疆閿欒锛坰ecret 涓嶅尮閰嶏級锛?> 闇€瑕佷汉宸ヤ慨澶嶉厤缃悗**鎵嬪伐閲嶆斁**銆?> 璇﹁ [OPERATIONS.md](docs/OPERATIONS.md) 绗?5.5 鑺傘€?
### payload 鐨勮鍓紙鍑忓皯鏁忔劅鏁版嵁鐣欏瓨锛?
```python
def _truncate_payload(event: dict[str, Any]) -> dict[str, Any]:
    """Webhook 鍘熸枃鍙兘寰堝ぇ锛屽彧淇濈暀鎺掗殰闇€瑕佺殑鍏抽敭瀛楁銆?""
    obj = event.get("data", {}).get("object", {}) or {}
    keep = {
        "id", "object", "status", "customer", "subscription", "amount_paid", "amount_due",
        "currency", "created", "current_period_start", "current_period_end", "cancel_at_period_end",
        "client_reference_id", "metadata", "billing_reason",
    }
    slim = {k: v for k, v in obj.items() if k in keep}
    return {"id": event.get("id"), "type": event.get("type"), "data": {"object": slim}}
```

**瀹夊叏鏀剁泭**锛歋tripe 鐨勪簨浠朵綋鍙兘鍖呭惈瀹㈡埛濮撳悕銆佸湴鍧€銆佸崱淇℃伅鎽樿绛夈€?瑁佹帀涓嶉渶瑕佺殑瀛楁闄嶄綆浜嗐€屼竴娆℃暟鎹簱娉勬紡銆嶇殑褰卞搷闈€?
> 鈿狅笍 **浣?`metadata` 琚畬鏁翠繚鐣欎簡**銆傝€?`create_checkout_session()` 浼氬線
> `metadata` 閲屽啓 `org_id` / `tier` / `user_id`鈥斺€?> 鍥犳 `webhook_events.payload` 閲屾湁鍐呴儴 ID銆?> 杩欎笉鏄瀵嗕俊鎭紙缁勭粐 ID 鍦?API 鍝嶅簲閲屼篃浼氬嚭鐜帮級锛?> 浣嗗鏋滀綘鏈変弗鏍肩殑鏁版嵁鏈€灏忓寲瑕佹眰锛岄渶瑕佽繘涓€姝ヨ鍓€?
### 骞傜瓑鐨勫弻灞備繚闄?
| 灞?| 鏈哄埗 |
|---|---|
| 1 | `uq_webhook_provider_event(provider, event_id)` |
| 2 | 璐︽湰 `idempotency_key`锛坄sub-activate:<sub_id>` / `invoice:<invoice_id>`锛?|

**绗?2 灞傜殑瀹夊叏浠峰€?*锛氬嵆浣跨 1 灞傝缁曡繃锛堜緥濡傝繍缁翠负浜嗛噸鏀捐€?鍒犻櫎浜?`webhook_events` 璁板綍锛夛紝棰濆害涔熶笉浼氶噸澶嶅彂鏀俱€?
### 浜嬩欢澶勭悊鐨勬巿鏉冩牎楠?
姣忎釜澶勭悊鍣ㄩ兘浼?*楠岃瘉浜嬩欢涓庣粍缁囩殑瀵瑰簲鍏崇郴**锛?
```python
async def _handle_checkout_completed(session, settings, obj) -> str:
    org_id = str(obj.get("client_reference_id") or (obj.get("metadata") or {}).get("org_id") or "")
    if not org_id:
        return "missing org reference"
    ...
```

璁㈤槄绫讳簨浠跺垯閫氳繃 `stripe_subscription_id` 鍙嶆煡锛坄_find_org_by_subscription`锛夛紝
鎵句笉鍒板氨杩斿洖 `"unknown subscription"`锛?*涓嶅仛浠讳綍淇敼**銆?
> 鈩癸笍 **娌℃湁瀵逛簨浠舵潵婧愬仛棰濆鐨勭櫧鍚嶅崟鏍￠獙**锛堜緥濡傞檺鍒?Stripe 鐨勬簮 IP锛夈€?> 瀹夊叏鎬у畬鍏ㄤ緷璧栫鍚嶉獙璇佲€斺€旇繖鏄?Stripe 瀹樻柟鎺ㄨ崘鐨勬ā寮忋€?
---

## 閿欒鍝嶅簲涓庝俊鎭硠闇?
**浠ｇ爜**锛歚api/errors.py`銆乣domain/errors.py`

### 缁熶竴缁撴瀯

```json
{
  "error": {"code": "...", "message": "...", "details": {}},
  "request_id": "..."
}
```

### 鏈鏈熷紓甯哥粷涓嶅娉勭粏鑺?
```python
async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
    """鍏滃簳锛氭湭棰勬湡寮傚父銆?
    瀵瑰鍙粰閫氱敤鏂囨 + request_id锛涘畬鏁村爢鏍堣繘鏃ュ織銆?    """
    log.exception("unhandled_exception",
                  path=request.url.path, method=request.method,
                  error_type=type(exc).__name__)
    return error_response(
        "internal_error",
        "鏈嶅姟寮€灏忓樊浜嗭紝璇风◢鍚庨噸璇曘€傝嫢鎸佺画鍑虹幇璇疯仈绯绘敮鎸佸苟鎻愪緵 request_id",
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
    )
```

**瀵瑰鍙湁閫氱敤鏂囨 + `request_id`**锛屽爢鏍堛€丼QL銆佹枃浠惰矾寰勩€?鍙橀噺鍊煎叏閮ㄥ彧杩涙棩蹇椼€傜敤鎴锋姤闅滄椂鎻愪緵 `request_id`锛岃繍缁存嵁姝ゅ叧鑱旀棩蹇椼€?
**鍏抽敭鍘熷垯**锛坄api/errors.py` 鐨勬ā鍧?docstring锛夛細

> **鍐呴儴寮傚父缁濅笉澶栨硠缁嗚妭**銆傛湭棰勬湡寮傚父缁熶竴杩斿洖 500 + 閫氱敤鏂囨锛?> 鐪熷疄鍫嗘爤鍙繘鏃ュ織锛屽苟鍦ㄥ搷搴旈噷甯︿笂 request_id 渚涙帓闅滃叧鑱斻€?
### 涓婃父閿欒涓嶈鐩存帴閫忎紶

`ProviderFailure` 琚爣璁颁负 `user_facing = False`锛?
```python
class ProviderFailure(AppError):
    """涓婃父渚涘簲鍟嗚皟鐢ㄥけ璐?""
    code = "provider_failure"
    http_status = 502
    user_facing = False

    def __init__(self, message: str = "", *, provider: str = "", retryable: bool = True) -> None:
        super().__init__(message or "涓婃父鐢熸垚鏈嶅姟鏆傛椂涓嶅彲鐢?,
                         details={"provider": provider, "retryable": retryable})
```

**榛樿娑堟伅鏄骇鍝佸寲鐨勬枃妗?*锛宍details.provider` 鏆撮湶渚涘簲鍟嗘爣璇?锛堣繖瀵圭敤鎴锋帓闅滄湁浠峰€硷紝涓斾緵搴斿晢韬唤涓嶇畻绉樺瘑锛夈€?
### 422 鏍￠獙閿欒鐨勫瓧娈靛寲

```python
fields = []
for err in exc.errors()[:10]:
    location = ".".join(str(p) for p in err.get("loc", []) if p not in ("body", "query", "path"))
    fields.append({"field": location or "body", "message": err.get("msg", "鏍￠獙澶辫触")})
```

**鏈€澶氳繑鍥?10 涓瓧娈甸敊璇?*锛堥槻姝㈡伓鎰忔瀯閫犺秴闀块敊璇垪琛ㄩ€犳垚鍝嶅簲浣撹啫鑳€锛夈€?`loc` 閲岀殑 `body` / `query` / `path` 鍓嶇紑琚幓鎺夛紝鍙暀瀛楁璺緞銆?
### 鏃ュ織涓殑璇锋眰涓婁笅鏂?
| 瀛楁 | 鏉ユ簮 |
|---|---|
| `request_id` | 涓棿浠剁粦瀹氾紙灏婇噸涓婃父 `X-Request-ID`锛屽惁鍒欑敓鎴?16 浣?hex锛?|
| `method` / `path` / `route` / `status` / `duration_ms` / `slow` | 璁块棶鏃ュ織 |
| `org_id` / `job_id` / `project_id` | 涓氬姟鏃ュ織鎸夐渶甯?|

**`path` 涓?`route` 鐨勫尯鍒?*锛堟寚鏍囧熀鏁版帶鍒讹級锛?
```python
route_obj = request.scope.get("route")
route = getattr(route_obj, "path", "") or request.url.path
```

- `path` 鏄湡瀹炶矾寰勶紙`/api/v1/projects/proj_01ABC...`锛夆€斺€?*杩涙棩蹇?*銆?- `route` 鏄矾鐢辨ā鏉匡紙`/api/v1/projects/{project_id}`锛夆€斺€?*杩涙寚鏍囨爣绛?*銆?
**涓轰粈涔堟寚鏍囩敤妯℃澘**锛坄metrics.py` 鐨?docstring锛夛細

> 鏍囩鍩烘暟蹇呴』鍙帶銆侶TTP 鎸囨爣涓€寰嬩娇鐢ㄨ矾鐢辨ā鏉胯€屼笉鏄湡瀹炶矾寰勶紝
> 鍚﹀垯姣忎釜 ID 閮戒細鍙樻垚涓€鏉℃椂闂村簭鍒楋紝鎶?Prometheus 鎵撶垎銆?
**鎺㈤拡涓庨潤鎬佽祫婧愰潤榛?*锛堜笉杩涜闂棩蹇楋級锛?`/health` `/healthz` `/readyz` `/livez` `/metrics` `/favicon.ico`
`/static/*` `/assets/*`銆?
### 涓€涓俊鎭毚闇茬偣锛堥渶璇勪及锛?
> 鈿狅笍 **`GET /api/v1/system/status` 涓?`GET /api/v1/system/ping` 鏃犻壌鏉冦€?*
>
> 瀹冧滑鐢?`DbSession` + `AppSettings`锛屼絾**涓嶆寕 `CurrentPrincipal`**銆?> 鍥犳浠讳綍鑳借闂湇鍔＄殑浜洪兘鑳借鍒帮細
> - `version` 涓?`env`锛堜究浜庨拡瀵规€ф敾鍑荤壒瀹氱増鏈級
> - `queues`锛?*鍏ㄥ簱**鐨勪换鍔¤鏁帮紝鏆撮湶浜嗗钩鍙扮殑鎬讳笟鍔￠噺锛?> - `config`锛坄safe_dump()`锛氫緵搴斿晢绫诲瀷銆佹槸鍚﹀惎鐢?billing/redis/storage 鍚庣锛?>
> 瀵嗛挜宸叉墦鐮侊紝鎵€浠?*涓嶆槸瀵嗛挜娉勬紡**銆備絾瀹冩槸涓€涓俊鎭敹闆嗛潰銆?>
> **寤鸿**锛氬湪鍙嶅悜浠ｇ悊灞傞檺鍒舵潵婧?IP锛屾垨鍘绘帀 `config` 瀛楁
> 锛堣 [DEPLOYMENT.md](docs/DEPLOYMENT.md) 绗?9.3 鑺傜殑 Nginx 绀轰緥锛夈€?
---

## 瀹¤鏃ュ織

**浠ｇ爜**锛歚services/audit.py`銆乣domain/enums.py::AuditAction`

### 璁板綍鐨勫唴瀹?
| 瀛楁 | 璇存槑 |
|---|---|
| `org_id` | 鎵€灞炵粍缁囷紙绯荤粺鍔ㄤ綔涓?`null`锛?|
| `actor_user_id` | 鎿嶄綔鑰?|
| `actor_type` | `user` / `api_key` / `system` |
| `action` | 16 绉嶅姩浣滅被鍨嬩箣涓€ |
| `target_type` / `target_id` | 鎿嶄綔鐩爣 |
| `ip` | 瀹㈡埛绔?IP |
| `user_agent` | |
| `request_id` | **鍙笌缁撴瀯鍖栨棩蹇椾覆鑱?* |
| `meta` | 鍔ㄤ綔鐩稿叧涓婁笅鏂?|

### 璁板綍鐨勫姩浣滐紙鍏ㄩ儴 16 绉嶏級

| 鍔ㄤ綔 | 瑙﹀彂鐐?| 鍏稿瀷 `meta` |
|---|---|---|
| `user.signup` | 娉ㄥ唽 | `{"plan": "free"}` |
| `user.login` | 鐧诲綍鎴愬姛 | 鈥?|
| `user.login_failed` | 鐧诲綍澶辫触 | `{"failures": N}` |
| `user.logout` | 鐧诲嚭 | `{"scope": "all_devices"}`锛堜粎鐧诲嚭鎵€鏈夎澶囨椂锛?|
| `user.password_changed` | 鏀瑰瘑 | 鈥?|
| `api_key.created` | 鍒涘缓 API Key | `{"prefix": "a1b2c3d4"}` |
| `api_key.revoked` | 鎾ら攢 API Key | 鈥?|
| `project.created` | 鍒涘缓椤圭洰 | `{"shots", "style", "estimated_credits"}` |
| `project.deleted` | 鍒犻櫎椤圭洰 | 鈥?|
| `shot.regenerated` | 閲嶈窇闀滃ご | `{"shots": [...]}` |
| `job.cancelled` | 鍙栨秷浠诲姟 | `{"count": N}` |
| `credits.granted` | 鍙戞斁棰濆害 | 鈥?|
| `subscription.changed` | 璁㈤槄鍙樻洿 | `{"tier", "status", "source"}` |
| `member.invited` | 娣诲姞鎴愬憳 | `{"role": "..."}` |
| `member.removed` | 绉婚櫎鎴愬憳 | 鈥?|
| `org.updated` | 缁勭粐鏇存柊 | 鈥?|

### 璁板綍鐨勮竟鐣岋紙閲嶈锛?
**瀹¤鍐欏叆涓嶅弬涓庝笟鍔′簨鍔＄殑鎴愯触**锛?
```python
"""瀹¤鏃ュ織銆?
鍝簺鎿嶄綔蹇呴』鐣欑棔锛氳璇併€佸瘑閽ャ€佽璐广€佹垚鍛樺彉鏇淬€侀」鐩垹闄ゃ€?瀹¤鍐欏叆**涓嶅弬涓庝笟鍔′簨鍔＄殑鎴愯触**鈥斺€斾笟鍔℃垚鍔熶絾瀹¤澶辫触鏃讹紝
瀹佸彲涓㈡帀涓€鏉″璁′篃涓嶈兘鍥炴粴鐢ㄦ埛鐨勬搷浣滐紱鍥犳鍗曠嫭鎻愪氦骞跺悶鎺夊紓甯搞€?"""
```

**鍥犳瀹¤鏃ュ織鍙兘鏈夌己澶便€?* 瀹冮€傚悎鐢ㄤ簬锛?
- 鉁?鎺掗殰锛堛€岃繖涓」鐩槸璋佸垱寤虹殑銆嶏級
- 鉁?寮傚父琛屼负鍙戠幇锛堛€屾湁浜哄湪鐭椂闂村唴鍒涘缓浜嗗緢澶?API Key銆嶏級
- 鉁?鐢ㄦ埛闂锛堛€屾垜鐨勯搴︿负浠€涔堝皯浜嗐€嶁€斺€旈厤鍚?`ledger_entries`锛?
**浣嗗畠涓嶉€傚悎浣滀负**锛?
- 鉂?**涓ユ牸鍚堣鐨勮瘉鎹摼**锛堝彲鑳芥湁缂哄彛锛屼笖鍙鏈夋暟鎹簱鏉冮檺鐨勪汉淇敼锛?- 鉂?**娉曞緥鍙栬瘉**锛堟病鏈夐槻绡℃敼鏈哄埗锛?
濡傛灉闇€瑕佷弗鏍煎悎瑙勶紝搴斿綋锛?1. 鎶婂璁″啓鍏ユ敼鎴愬悓姝ラ樆濉?+ 澶辫触鍗冲洖婊氾紙鐗虹壊鍙敤鎬э級锛?2. 鎴栬€呭啓鍏ョ嫭绔嬬殑銆乤ppend-only 鐨勯珮鍙敤鏃ュ織绯荤粺锛堝 WORM 瀛樺偍锛夈€?
**杩欎袱鑰呭綋鍓嶉兘鏈疄鐜般€?*

### 瀹¤鏃ュ織鐨勮闂帶鍒?
鍙湁**娴忚鍣ㄤ細璇?*鑳借锛?
```python
if principal.kind == "api_key":
    raise Forbidden("瀹¤鏃ュ織浠呭娴忚鍣ㄤ細璇濆紑鏀?)
```

**鐞嗙敱**锛氬璁℃棩蹇楀寘鍚?IP銆佹搷浣滆€呫€佹垚鍛樺彉鏇村巻鍙测€斺€?鏄敾鍑昏€呭仛渚﹀療锛堜簡瑙ｇ粍缁囩粨鏋勩€佹壘鍒伴珮鏉冮檺鐢ㄦ埛锛夌殑鐞嗘兂淇℃伅婧愩€?
> 鈿狅笍 **浣嗗璁＄鐐规病鏈夊仛瑙掕壊妫€鏌?*鈥斺€斾换浣曚細璇濈敤鎴凤紙鍖呮嫭 `member`锛?> 閮借兘璇昏嚜宸辩粍缁囩殑鍏ㄩ儴瀹¤鏃ュ織銆傝繖閫氬父鏄彲鎺ュ彈鐨?> 锛堝悓涓€缁勭粐鐨勬垚鍛樻湰鏉ュ氨鑳界湅鍒板ぇ閮ㄥ垎涓氬姟鏁版嵁锛夛紝
> 浣嗗鏋滀綘鏈夋洿涓ユ牸鐨勫彲瑙佹€ц姹傦紝闇€瑕佽ˉ `AdminPrincipal`銆?
### 娌℃湁瑕嗙洊鐨勫姩浣?
浠ヤ笅鍔ㄤ綔**褰撳墠涓嶅啓瀹¤**锛?
- 椤圭洰鍐呭鏇存柊锛坄update_project` 鐨勬爣棰?椋庢牸鍙樻洿锛?- 闀滃ご鎻愮ず璇嶆敼鍐欙紙`update_shot_prompts`锛?- 浜х墿鍒嗕韩鐨勫紑鍚?鍏抽棴锛坄toggle_share`锛?- 浜х墿涓嬭浇锛堝彧鏈?`download_count` 璁℃暟锛?- BYOK 瀵嗛挜鐨勬洿鏂帮紙`settings_byok`锛?- 鐧诲綍澶辫触鐨?*闄愭祦鎷︽埅**锛堝彧鎵撴寚鏍囷紝涓嶅啓瀹¤锛?- 缁勭粐鍋滅敤/鍚敤锛堟棤瀹¤锛屼篃娌℃湁瀵瑰簲鐨勬灇涓惧€硷級

---

## 鐢熶骇鐜鐨勫己鍒堕厤缃?
**浠ｇ爜**锛歚config.py::Settings._validate_environment`

`REEL_ENV=prod` 鏃讹紝**鍏」纭€ц姹備换涓€涓嶆弧瓒冲垯鎷掔粷鍚姩**锛?
| # | 瑕佹眰 | 澶辫触娑堟伅 | 瀹夊叏鎰忎箟 |
|---|---|---|---|
| 1 | `REEL_SECURITY__SECRET_KEY` 闈炵┖ | `REEL_SECURITY__SECRET_KEY 鏈厤缃甡 | 鍚﹀垯浼氳瘽绛惧悕瀵嗛挜鏄殢鏈虹敓鎴愮殑锛岃繘绋嬮噸鍚悗鎵€鏈変細璇濆け鏁堬紙涓斿鍓湰涔嬮棿浜掍笉璁ゅ彲锛?|
| 2 | 鏁版嵁搴撲笉鏄?SQLite | `鐢熶骇鐜涓嶅簲浣跨敤 SQLite锛岃閰嶇疆 REEL_DATABASE__URL` | SQLite 鏃犲苟鍙戝啓鑳藉姏銆佹棤缃戠粶璁块棶鎺у埗銆佹棤鐢ㄦ埛鏉冮檺浣撶郴 |
| 3 | `REEL_SECURITY__HTTPS_ONLY=true` | `鐢熶骇鐜蹇呴』寮€鍚?REEL_SECURITY__HTTPS_ONLY=true` | 鍚﹀垯 Cookie 娌℃湁 `Secure` 灞炴€с€丠STS 涓嶄笅鍙戯紙浼氳瘽鍙涓棿浜虹獌鍙栵級 |
| 4 | `allowed_hosts` 涓嶅惈 `*` | `鐢熶骇鐜蹇呴』鏄惧紡閰嶇疆 REEL_SECURITY__ALLOWED_HOSTS` | 鍚﹀垯 `TrustedHostMiddleware` 涓嶅惎鐢紙Host 澶存敞鍏ワ級 |
| 5 | `base_url` 涓嶆槸 `http://` | `鐢熶骇鐜 REEL_BASE_URL 蹇呴』鏄?https` | 鍒嗕韩閾炬帴銆丼tripe 鍥炶皟鍦板潃浼氭寚鍚?http |
| 6 | 鑷冲皯閰嶇疆浜嗚棰戜緵搴斿晢 Key | `鏈厤缃换浣曡棰戜緵搴斿晢 Key锛圧EEL_PROVIDERS__VIDEO__API_KEY锛夛紝浜у搧灏嗘棤娉曚骇鍑烘垚鐗嘸 | 杩欐槸浜у搧鍙敤鎬ф牎楠岋紙涔熼棿鎺ラ槻姝€岀敤 mock 鍐掑厖鐪熷疄鐢熸垚銆嶏級 |

**璁捐鍘熷垯**锛堜唬鐮佹敞閲婂師璇濓級锛?
> 鐢熶骇鐜蹇呴』鍦ㄥ惎鍔ㄦ椂灏辨弧瓒崇‖鎬ц姹傗€斺€斿畞鍙捣涓嶆潵锛屼篃涓嶈甯︾梾涓婄嚎銆?
**澶辫触杈撳嚭褰㈡€?*锛?
```
閰嶇疆鏍￠獙鏈€氳繃锛堢幆澧?prod锛夛細
  - REEL_SECURITY__SECRET_KEY 鏈厤缃?  - 鐢熶骇鐜蹇呴』寮€鍚?REEL_SECURITY__HTTPS_ONLY=true
  - ...
```

锛?*涓€娆℃€у垪鍑烘墍鏈夐棶棰?*锛岃€屼笉鏄€愪釜鎶ラ敊锛岄伩鍏嶅杞噸鍚€傦級

### 鍏跺畠鍚姩鏈熸牎楠?
| 鏍￠獙 | 浣嶇疆 | 澶辫触娑堟伅 |
|---|---|---|
| S3 鍚庣蹇呴』鏈?bucket | `StorageSettings._check_s3` | `storage.backend=s3 鏃跺繀椤婚厤缃?s3_bucket` |
| 鍚敤璁¤垂蹇呴』鏈?Stripe Key | `BillingSettings._check` | `billing.enabled=true 鏃跺繀椤婚厤缃?stripe secret_key` |
| 蹇呴渶琛ㄥ瓨鍦?| `api/app.py::_ensure_schema` | `鏁版嵁搴撶己灏戝繀瑕佽〃锛?..銆傝鍏堟墽琛?alembic upgrade head銆俙 |

**鐢熶骇鐜鐨?schema 绛栫暐鏄€屽彧妫€鏌ヤ笉淇敼銆?*锛?
```python
if settings.is_production:
    raise RuntimeError(f"鏁版嵁搴撶己灏戝繀瑕佽〃锛歿sorted(missing)}銆傝鍏堟墽琛?`alembic upgrade head`銆?)
log.warning("auto_creating_schema", missing=sorted(missing), reason="闈炵敓浜х幆澧?)
await create_all()
```

**鐞嗙敱**锛堜唬鐮佹敞閲婏級锛氥€岀粨鏋勫彉鏇村繀椤昏蛋 Alembic锛?鍚﹀垯涓€娆¤閮ㄧ讲灏卞彲鑳芥倓鎮勬敼鎺夌嚎涓婅〃缁撴瀯銆傘€?
> 鈿狅笍 **娉ㄦ剰**锛氶潪鐢熶骇鐜浼氳嚜鍔?`create_all()`銆?> 杩欐剰鍛崇潃**鍦?staging 鐜閲岋紝浠ｇ爜閲岀殑妯″瀷鍙樻洿浼氳嚜鍔ㄥ簲鐢ㄥ埌鏁版嵁搴?*銆?> 濡傛灉 staging 鐢ㄧ殑鏄敓浜х殑鏁版嵁搴撳壇鏈紝浼氶€犳垚缁撴瀯婕傜Щ銆?> 璇风‘淇?staging 鐢ㄨ嚜宸辩殑鏁版嵁搴撱€?
### 寤鸿棰濆閰嶇疆鐨勫畨鍏ㄧ浉鍏抽」

浠ヤ笅**涓嶆槸鍚姩寮哄埗**锛屼絾鐢熶骇鐜寮虹儓寤鸿锛?
| 閰嶇疆 | 寤鸿鍊?| 鍘熷洜 |
|---|---|---|
| `REEL_SECURITY__ALLOW_SIGNUP` | `false`锛堥櫎闈為渶瑕佸叕寮€娉ㄥ唽锛?| 鍏抽棴鍏紑娉ㄥ唽锛屽彧鍏佽鍐呴儴閭€璇?|
| `REEL_SECURITY__SIGNUP_INVITE_CODE` | 璁剧疆涓€涓己闅忔満鍊?| 閭€璇峰埗娉ㄥ唽锛涙瘮杈冪敤 `hmac.compare_digest`锛堝父鏁版椂闂达級 |
| `REEL_SECURITY__MIN_PASSWORD_LENGTH` | `12` 鎴栨洿楂?| 榛樿 10 |
| `REEL_RATE_LIMIT__ENABLED` | `true` | 榛樿灏辨槸 true锛屽埆鍏?|
| `REEL_REDIS__ENABLED` | `true`锛堝鍓湰鏃讹級 | 鍚﹀垯闄愭祦鍚勫壇鏈嫭绔嬭鏁?|
| `REEL_OBSERVABILITY__LOG_FORMAT` | `json` | 渚夸簬閲囬泦涓庤劚鏁忛摼璺敓鏁?|
| `REEL_STORAGE__BACKEND` | `s3`锛堝鍓湰鏃讹級 | local 鍚庣鍦ㄥ鍓湰涓嬩細涓嶄竴鑷?|
| `REEL_EMAIL__ENABLED` | 鎸夐渶 | 鍏抽棴鏃堕偖浠跺彧钀藉簱鎵撴棩蹇楋紙涓嶄細鐪熺殑鍙戝嚭鍘伙級 |

**閭€璇风爜鐨勬瘮杈冩槸甯告暟鏃堕棿鐨?*锛?
```python
expected_invite = settings.security.signup_invite_code.get_secret_value()
if expected_invite:
    import hmac
    if not hmac.compare_digest(invite_code or "", expected_invite):
        raise Forbidden("閭€璇风爜涓嶆纭?)
```

---

## 宸茬煡鐨勫畨鍏ㄨ€冮噺涓庡緟鏀硅繘椤?
浠ヤ笅闂鍦?1.0.0 鏃剁偣**纭瀛樺湪**銆傚畠浠笉鏄€屽彲鑳界殑椋庨櫓銆嶏紝鑰屾槸浠ｇ爜閲岀殑瀹為檯鐘舵€併€?
### 馃敶 闇€瑕佸湪涓婄嚎鍓嶅缃?
#### 1. 鎺у埗鍙版ā鏉夸笌闈欐€佽祫婧愮己澶憋紝瀵艰嚧 CSP 绾︽潫鏃犳硶楠岃瘉

`reelagent_cloud/web/templates/` 涓?`reelagent_cloud/web/static/`
**涓や釜鐩綍閮戒笉瀛樺湪**銆傚悗鏋滐細

- 鎵€鏈?HTML 椤甸潰璺敱锛坄/`銆乣/pricing`銆乣/login`銆乣/app/*`锛夊湪杩愯鏃舵姏
  `TemplateNotFound`銆?- `/static` 涓嶄細琚寕杞斤紙`create_app()` 閲屾湁 `if STATIC_DIR.exists()` 鍒ゆ柇锛夈€?- **CSP 鐨勩€宍script-src 'self'`锛岀姝㈠唴鑱旇剼鏈€嶈繖鏉＄害鏉?  鍦ㄥ綋鍓嶄粨搴撶姸鎬佷笅鏃犳硶琚疄闄呴獙璇?*鈥斺€斿洜涓烘病鏈夋ā鏉裤€佹病鏈夊墠绔?JS銆?
杩欐棦鏄姛鑳界己鍙ｏ紝涔熸槸**瀹夊叏楠岃瘉鐨勭己鍙?*锛?灏嗘潵琛ラ綈妯℃澘鏃讹紝蹇呴』纭繚鎵€鏈?JS 閮芥槸澶栭儴鏂囦欢锛?鍚﹀垯 CSP 浼氭嫤鎴紙杩欏彲鑳借璇垽涓恒€孋SP 閰嶉敊浜嗐€嶈€屾斁瀹斤紝浠庤€?姘镐箙澶卞幓杩欐潯闃茬嚎鈥斺€斾唬鐮佹敞閲婇噷鏄庣‘璀﹀憡浜嗚繖涓闄╋級銆?
#### 2. `/api/v1/system/status` 鏃犻壌鏉?
瑙乕绗?9 鑺俔(#涓€涓俊鎭毚闇茬偣闇€璇勪及)鐨勮鏄庛€?鏆撮湶 `version` / `env` / 鍏ㄥ簱闃熷垪娣卞害 / 閰嶇疆蹇収锛堝凡鎵撶爜锛夈€?寤鸿鍦ㄥ弽鍚戜唬鐞嗗眰闄愬埗鏉ユ簮 IP銆?
#### 3. `Storage` 鎶借薄鐨勭鎴锋牎楠屾湭涓嬫矇

褰撳墠渚濊禆銆屾瘡涓矾鐢遍兘璁板緱璋?`key_belongs_to_org`銆嶃€?杩欐槸 code review 灞傞潰鐨勭害鏉燂紝鑰屼笉鏄被鍨嬪眰闈㈢殑淇濊瘉銆?
**寤鸿鏀归€犳柟鍚?*锛氭妸 `org_id` 鍋氭垚 `Storage` 鎶借薄鎵€鏈夋柟娉曠殑蹇呭～鍙傛暟锛?
```python
async def get_bytes(self, key: str, *, org_id: str) -> bytes:
    self._assert_owner(key, org_id)
    ...
```

杩欐牱銆屽繕璁版牎楠屻€嶅湪绫诲瀷灞傞潰灏变笉鍙兘鍙戠敓銆?褰卞搷闈細8 涓娊璞℃柟娉?+ 鑻ュ共杈呭姪鏂规硶锛屽洜姝ゅ垪鍏ュ悗缁紨杩?锛堣 [ADR 0007](docs/adr/0007-tenant-scoped-artifact-storage-keys.md) 鐨勬柟妗?E锛夈€?
### 馃煚 闇€瑕佽瘎浼?
#### 4. `X-Org-Id` 鍒嗘敮涓嶆鏌?`is_suspended`

`_resolve_org()` 鐨?`requested` 鍒嗘敮锛堟樉寮忎紶 `X-Org-Id`锛?**涓嶆鏌?`org.is_suspended`**锛屽彧鏈夐粯璁ょ粍缁囪矾寰勬墠妫€鏌ャ€?
**缂撹В鎺柦**锛氬仠鐢ㄧ粍缁囨椂鍚屾椂鍚婇攢鍏?API Key
锛堣 [OPERATIONS.md](docs/OPERATIONS.md) 绗?3.4 鑺傦級銆?API Key 璺緞鏈韩浼氬湪 `_principal_from_api_key` 閲屾鏌?`is_suspended`銆?
#### 5. 鏃跺簭渚т俊閬擄細鏈煡鐢ㄦ埛鐨勭櫥褰曞垎鏀洿鎱?
```python
if user is None:
    verify_password(password, hash_password("dummy-password-for-timing"))
```

姣忔閮戒細**閲嶆柊璁＄畻涓€娆″搱甯?*锛堣€屼笉鏄敤涓€涓璁＄畻鐨勫父閲忥級锛?鍥犳璇ュ垎鏀瘮銆岀敤鎴峰瓨鍦ㄤ絾瀵嗙爜閿欒銆嶆洿鎱€傜悊璁轰笂鍙祴閲忋€?
**淇鏂瑰悜**锛氭ā鍧楃骇棰勮绠椾竴涓父閲忓搱甯岋紝澶嶇敤瀹冦€?
#### 6. `API_KEY_PEPPER` 涓?`SECRET_KEY` 鐨勮€﹀悎锛堥潪鐢熶骇鐜锛?
```python
else:
    if not self.security.api_key_pepper.get_secret_value():
        self.security.api_key_pepper = SecretStr(
            self.security.secret_key.get_secret_value() + ":apikey"
        )
```

**闈炵敓浜х幆澧?*鏈厤缃?`API_KEY_PEPPER` 鏃跺畠浼氳窡闅?`SECRET_KEY`銆?鐢熶骇鐜涓嶅彈褰卞搷锛堝惎鍔ㄦ牎楠屼笉寮哄埗瀹冿紝浣嗗鏋滅暀绌猴紝
`_pepper()` 浼氬洖閫€鍒?`secret_key`鈥斺€旇繖涓€鐐归渶瑕佹樉寮忛厤缃互淇濇寔鐙珛杞崲鑳藉姏锛夛細

```python
def _pepper(settings: Settings) -> str:
    pepper = settings.security.api_key_pepper.get_secret_value()
    return pepper or settings.security.secret_key.get_secret_value()
```

**寤鸿**锛氱敓浜х幆澧?*鏄惧紡閰嶇疆** `REEL_SECURITY__API_KEY_PEPPER`锛?浠ヤ究涓?`SECRET_KEY` 鐙珛杞崲銆?
#### 7. BYOK 瑙ｅ瘑澶辫触鐨勯潤榛樺閿?
`decrypt_byok_map` 瀵硅В涓嶅紑鐨勬潯鐩潤榛樿烦杩囷紙`except TokenError: continue`锛夛紝
瀵艰嚧 `SECRET_KEY` 杞崲鍚庣殑鐥囩姸鏄€岀敓鎴愪笉浜嗙墖瀛愩€嶈€屼笉鏄槑纭殑閰嶇疆閿欒銆?
**寤鸿**锛氬湪 `resolve_engine_mode()` 閲岋紝
褰?org 鐨?`byok` 瀛楁闈炵┖浣嗚В瀵嗙粨鏋滀负绌烘椂锛屾墦涓€鏉℃槑纭殑璀﹀憡鏃ュ織
锛堛€孊YOK 瀵嗛挜瀛樺湪浣嗗叏閮ㄨВ瀵嗗け璐ワ紝SECRET_KEY 鍙兘宸茶疆鎹€嶏級銆?
#### 8. 闄愭祦瑕嗙洊涓嶅畬鏁?
`anonymous` 涓?`user` 涓や釜 scope 瀹氫箟浜嗕絾**娌℃湁璋冪敤鐐?*銆?鍥犳鍙湁 `POST /login`銆乣POST /signup`銆乣POST /api/v1/projects`
涓変釜绔偣鍙椾繚鎶わ紝鍏跺畠绔偣锛堝寘鎷墍鏈?`GET`锛変笉鍙楅檺娴併€?
**椋庨櫓**锛氭湭鍙椾繚鎶ょ殑楂橀鏌ヨ鍙兘閫犳垚鏁版嵁搴撳帇鍔涳紱
鍒楄〃绫荤鐐瑰彲琚敤浜庢壒閲忔姄鍙栥€?
#### 9. `reelagent_credits_consumed_total` 鐨?`org_id` 鏍囩

绉熸埛鏁伴噺澶氭椂浼氶€犳垚 Prometheus 鍩烘暟鑶ㄨ儉
锛堣繖鏄竴涓?DoS 闈細澶ч噺绉熸埛 = 澶ч噺鏃堕棿搴忓垪锛夈€?
**寤鸿**锛氬湪閲囬泦渚у仛 `sum without (org_id)`锛屾垨鍒犻櫎璇ユ爣绛俱€?
#### 10. 瀹¤鏃ュ織鍙鏈夋暟鎹簱鏉冮檺鐨勪汉淇敼

娌℃湁闃茬鏀规満鍒讹紙鏃犲搱甯岄摼銆佹棤 WORM 瀛樺偍銆佹棤绛惧悕锛夈€?
**椋庨櫓**锛氬唴閮ㄤ汉鍛樺彲浠ュ垹闄ゆ垨淇敼涓嶅埄浜庤嚜宸辩殑瀹¤璁板綍銆?
**寤鸿**锛氬鏋滆繖鏄▉鑳佹ā鍨嬬殑涓€閮ㄥ垎锛岄渶瑕佹妸瀹¤鍐欏叆鐙珛鐨?append-only 绯荤粺銆?
### 馃煛 褰撳墠涓嶆敮鎸侊紙鏄庣‘鐨勭己鍙ｏ級

| 鑳藉姏 | 鐘舵€?|
|---|---|
| 鍙屽洜绱犺璇侊紙2FA / TOTP锛?| 鉂?涓嶆敮鎸?|
| OAuth / SSO 鐧诲綍 | 鉂?涓嶆敮鎸?|
| 缁勭粐绾ф湇鍔¤处鍙凤紙鐙珛浜?API Key锛?| 鉂?涓嶆敮鎸?|
| 鏈嶅姟绔?session 琛紙鍗曠嫭鍚婇攢鏌愪釜浼氳瘽锛?| 鉂?涓嶆敮鎸侊紙鍙湁銆岀櫥鍑烘墍鏈夎澶囥€嶏級 |
| 浼氳瘽鐨?IP / UA 缁戝畾 | 鉂?涓嶆敮鎸?|
| WAF | 鉂?涓嶅惈锛堥渶鍦ㄩ儴缃蹭晶瑙ｅ喅锛?|
| IP 鐧藉悕鍗?/ 榛戝悕鍗?| 鉂?涓嶆敮鎸?|
| 鎸夌鎴风殑瀛樺偍鍑瘉涓庨厤棰?| 鉂?涓嶆敮鎸侊紙鍏变韩 bucket锛?|
| PostgreSQL 琛岀骇瀹夊叏锛圧LS锛?| 鉂?涓嶆敮鎸侊紙搴旂敤灞傞殧绂伙級 |
| 鎸夌鎴风殑鍔犲瘑瀵嗛挜 | 鉂?涓嶆敮鎸?|
| KMS / Vault 闆嗘垚 | 鉂?涓嶆敮鎸侊紙浠庣幆澧冨彉閲忚锛?|
| 瀵嗛挜杞崲鐨勫弻瀵嗛挜杩囨浮 | 鉂?涓嶆敮鎸?|
| CSP 涓婃姤绔偣 | 鉂?涓嶆敮鎸?|
| 涓ユ牸鐨勫悎瑙勮瘉鎹摼 | 鉂?涓嶆敮鎸侊紙瀹¤鍙兘缂哄け涓斿彲淇敼锛?|
| HIBP / 澶у瓧鍏稿急鍙ｄ护妫€娴?| 鉂?涓嶆敮鎸侊紙鍙湁 16 涓瘝鐨勭‖缂栫爜榛戝悕鍗曪級 |
| 鍙嶈嚜鍔ㄥ寲锛圕APTCHA / 璁惧鎸囩汗锛?| 鉂?涓嶆敮鎸?|
| 閫熺巼闄愬埗鐨?fail-closed 妯″紡 | 鉂?涓嶆敮鎸侊紙鍥哄畾 fail-open锛?|
| 鏁版嵁搴撹繛鎺ュ姞瀵嗭紙TLS 鍒?PG锛?| 鈿狅笍 鍙栧喅浜庝綘鐨?`REEL_DATABASE__URL` 鍙傛暟锛堝簲鐢ㄦ湭寮哄埗瑕佹眰 SSL锛?|
| 瀵硅薄瀛樺偍鏈嶅姟绔姞瀵?| 鈿狅笍 鍙栧喅浜庝綘鐨?bucket 閰嶇疆锛堝簲鐢ㄦ湭璁剧疆鍔犲瘑鍙傛暟锛?|

### 閮ㄧ讲渚у繀椤昏嚜琛屼繚璇佺殑瀹夊叏瑕佹眰

杩欎簺**搴旂敤鏃犳硶鑷瘉**锛屽繀椤荤敱閮ㄧ讲閰嶇疆淇濊瘉銆備换浣曚竴鏉′笉婊¤冻閮戒細
瀹炶川鎬у墛寮变笂闈㈡墍鏈夌殑闃叉姢锛?
1. **搴旂敤绔彛涓嶅鍏綉寮€鏀?*锛堥槻 X-Forwarded-For 浼€犵粫杩?IP 闄愭祦锛夈€?2. **鍙嶅悜浠ｇ悊涓嶆敼鍐欒姹備綋**锛圫tripe 楠岀渚濊禆锛夈€?3. **鍙嶅悜浠ｇ悊涓嶆妸 5xx 閲嶅啓鎴?200**锛圫tripe 閲嶈瘯渚濊禆锛夈€?4. **鏁版嵁搴撲笉鏆撮湶鍒板叕缃?*锛屼笖浣跨敤寮哄彛浠ゃ€?5. **瀵硅薄瀛樺偍 bucket 涓虹鏈?*锛屼笖澶囦唤鍓湰鍦ㄧ嫭绔嬫潈闄愯竟鐣屽唴銆?6. **`SECRET_KEY` 涓嶅湪浠ｇ爜浠撳簱銆佷笉鍦ㄩ暅鍍忋€佷笉鍦ㄦ棩蹇楅噷**锛屽苟宸插畨鍏ㄥ浠姐€?7. **鏃ュ織閲囬泦绯荤粺鏈韩鏄彈璁块棶鎺у埗鐨?*锛堟棩蹇楅噷鏈?IP銆侀偖绠便€佺粍缁?ID锛夈€?8. **瀹夸富鏈轰笌瀹瑰櫒闀滃儚瀹氭湡鎵撹ˉ涓?*銆?9. **`REEL_SUPPORT_EMAIL` 鏀规垚鐪熷疄閭**锛堝惁鍒欐紡娲炴姤鍛婃敹涓嶅埌锛夈€?10. **TLS 姝ｇ‘閰嶇疆**锛堣瘉涔︽湁鏁堛€佹棤寮卞崗璁€佹棤寮卞瘑鐮佸浠讹級銆?
---

## 鑷磋阿

鎰熻阿鎵€鏈夎礋璐ｄ换鍦版姤鍛婂畨鍏ㄩ棶棰樼殑浜恒€傚湪琛ヤ竵鍙戝竷鍚庯紝
鎴戜滑浼氬湪鏈枃浠讹紙鎴栧搴旂殑鍙戝竷璇存槑锛夐噷鑷磋阿鈥斺€旈櫎闈炰綘甯屾湜淇濇寔鍖垮悕銆?
> 鏈妭褰撳墠涓虹┖锛屽洜涓?`1.0.0` 鏄涓寮忕増鏈紝灏氭湭鏀跺埌婕忔礊鎶ュ憡銆?
---

## 鐩稿叧鏂囨。

- [鏋舵瀯璇存槑](docs/ARCHITECTURE.md) 鈥斺€?閴存潈妯″瀷銆佺鎴烽殧绂汇€佹暟鎹ā鍨?- [閮ㄧ讲鎸囧崡](docs/DEPLOYMENT.md) 鈥斺€?鐢熶骇閰嶇疆銆乀LS銆佷唬鐞嗐€丼tripe
- [杩愮淮 Runbook](docs/OPERATIONS.md) 鈥斺€?浜嬫晠澶勭疆锛堝惈瓒婃潈灏濊瘯鐨勬帓鏌ワ級
- [ADR 0004](docs/adr/0004-api-key-cannot-manage-billing.md) 鈥斺€?涓轰粈涔?API Key 涓嶈兘绠¤璐?- [ADR 0007](docs/adr/0007-tenant-scoped-artifact-storage-keys.md) 鈥斺€?涓轰粈涔堝瓨鍌?key 涓庣鎴风粦瀹?- [璐＄尞鎸囧崡](CONTRIBUTING.md)
