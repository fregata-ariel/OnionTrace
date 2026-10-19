# M1/T9: CIと固定版OAIの総合受入

T9 (#11、親 #12) はT3〜T8を一巡させる。M1の受入は完了し、mainへ統合済み。
操作手順は[実行マニュアル](runbook.md)、完了時の範囲・証拠は[M1受入結果](m1-results.md)を参照する。通常の必須CIは小さなfixtureだけを
使い、OAIの取得・USRP・RFドライバ・実softmodemの実行に依存しない。
`scripts/ci.py`はskipを失敗とし、既存のwrapper、callback/inline、Queueの別名・応答、
未解決・入力変更の回帰に加えて、両main → thread/Queue/callback → CFG → HTMLを
`onion.acceptance`で検証する。`out/acceptance`も通常のArtifactsに含む。
再構成・探索の複数run回帰は`test_model_native.py`で同じnative抽出から実行する。
fixtureの合格をOAIの合格とは扱わない。

## 固定OAI workflow

GitHub Actionsの **OAI pinned acceptance** (`.github/workflows/oai.yml`) を
`workflow_dispatch`で対象refを選んで実行する。mainへ統合済みで、Actionsの手動実行一覧から
利用できる。workflow/実行driver自体を変更する指定ファイルのPRでも
別workflowとして実行し、通常の5必須ジョブからは分離する。
Ubuntu 24.04の使い捨てrunner、Python 3.12、Clang/LLVM 18、CMake/Ninjaを使う。
ネットワーク、約14GB以上の空き容量、最大180分が必要。

1. RFドライバを含まないOS依存を導入し、LibTooling抽出器をビルドする。
2. `scripts/install_oai_dependencies.py`で`config/oai-2026.w07.json`のASN.1とSIMDeを
   commit指定で取得する。ソース・インストール先を専用prefixへ置き、版と実ファイルを記録する。
   SIMDeはbuild_helperの`/usr/include/simde`と同じsystem-header区分で渡す。
   実ファイルhashは記録するが、外部intrinsicsをOAIの関数定義として展開しない。
3. `scripts/oai_ci.py`が**存在しない出力先**を作り、固定origin/tag/commitの新規checkoutを取得する。
   既存checkout・運用buildは再利用しない。
4. 固定`build_oai`引数（gNB、nrUE、SIMU、Clang 18、RelWithDebInfo、ccache無効、`--noavx512`）で
   configure/buildする。File API queryはconfigure前に置く。さらにCMake buildの終了値と
   両実行ファイルの存在・hashを確認する。build_oaiの成功メッセージだけでは合格させない。
   固定版のLDPC encoderはAVX-512 VBMI経路でClang 18 backendがクラッシュしたため、
   OAIの正式optionでAVX-512を無効にする。OAIソースは変更しない。
   CPU条件はC/C++とも`-march=x86-64-v3 -mgfni -mvpclmulqdq`に固定する。
   OAIが先頭へ加える`-march=native`の後に、正式な`CMAKE_C_FLAGS`／`CMAKE_CXX_FLAGS`で
   この条件を渡す。GFNIとVPCLMULQDQはOAIがnative SIMDeとして要求する機能に合わせる。
   `--noavx512`だけではホストによって後続の`-march=native`がAVX-512/AVX10系の条件を
   有効にし、Clang 18の`evex512`不整合でビルドが失敗したため、最終CPU条件も固定する。
   元の全引数とClang `-###`の最終CPU/feature展開はmanifestに保持する。
5. T2のOAI profileで両ターゲットの依存閉包と全compile DBを照合し、
   `config/oai-analysis-scope.json`の10翻訳単位について入力検証とT3を実行する。
   固定source auditの全Cソースを含み、ヘッダは実際の依存として検証する。
   このsubset以外もmanifestから削除せず、未解析の除外理由を明記する。
   requested sourceが依存閉包に無い場合は失敗。同一sourceの複数条件は全て保持する。
   manifestのコンパイル条件を削ったり、生成ヘッダ不足を握りつぶしたりしない。
6. 各targetについてT4/T5/T6 → T1/T7 → T8のworkspace・構造モデル・表示projection・
   offline HTMLを出力する。T4の5経路、T5の13地点、T6のソース18地点中の意味地点と
   6候補接続を固定byte位置・hashから再計算する。contract文は別途source hashで確認する。
   同一workspace内で直前に検証したthread/callback graphを共有し、重複探索を避ける。

OAIのHTMLと`view.json`のCFG本文は、固定thread経路のcaller/start、callback/Queue監査地点の
関数とcallback候補関数に限定する。全構造の関係は残し、表示対象外の関数を未解決とは表示しない。
`display_scope`に関数名・表示数・全抽出数・対象外の関数IDを記録する。全関数のCFGと文脈は
`workspace.json`とnative抽出に保持する。全関数のCFGを両targetの単一HTMLへ埋め込むと約754MBに
なり、JavaScript処理系の単一文字列上限を超えるため、監査対象の表示と完全な保存形式を分ける。
埋込みデータは`application/json`から`JSON.parse`で読み込み、大きなJavaScriptリテラルの
コンパイルを避ける。`void(void)`と古いCの`void()`の互換性は、実引数・定義の引数がともに0件の
直接呼出しだけで扱い、成立根拠をcall relationに記録する。余分な実引数や複数定義を確定しない。

CIは`--capture-only`でnative抽出まで実行して`oai-native-*` Artifactを先に保存し、
別stepの`--resume-acceptance`で総合gateを実行する。前半だけでは`run.json.passed`はfalseのまま。
再開時にOnionTrace commit、manifest、抽出一覧、両実行ファイルのhashを照合し、各nativeファイルも
モデル構築時に再検証する。再構成stepは20分・仮想メモリ6GiBを上限とし、runner停止前に失敗を
診断できるようにする。native Artifactは総合受入の代わりにはならない。
CIでは`--resume-acceptance --require-browser`で意味解析の合格後も全体をpendingに保つ。
続くChromium検証が実OAI HTMLを開き、両mainから開始関数・Queue参照・実CFGへ移動し、
実行観測への誤表示や外部通信が無いことを確認する。script error・表示上限で選択経路へ進めない場合も失敗。
完了通知の取得側`wait_free_rx_tti`も開き、Node/Chromiumの版、結果JSONと5枚の画面を
`browser/`へ保存する。大きな枝は16接続ずつ前後へ移動でき、240要素の描画上限で後半の接続を隠さない。
consumer関数から入力Queueへも取得方向を示して移動できる。ここまで成功した場合だけ
`run.json.passed`と`browser_verified`をtrueにする。通常CIは引き続きOAIを取得しない。
大きなJSONの書出し・canonical hash・ファイルhashは逐次処理し、読込みでは同じpath/ID文字列を共有する。
I/Oの変更はcanonical IDとJSONの値を変えない。OAIのworkspace/structure/viewは空白を省いた
JSONで保存する。同期子参照は親参照IDとcall pathで文脈を保持し、親の本文を再帰コピーしない。
ナビゲーション上限（既定10,000件）は新しく展開する同期子参照を数え、元のcall-site一覧は数えない。
上限・再帰/深さで止めた場合はincompletenessを残し、総合gateを不合格にする。
Clangが到達不能と記録した空の後続先は、
`AdjacentBlock::isReachable()`で判別する。`do { ... } while (0)`の既知の剪定を未知のCFGとして
関数全体へ波及させず、到達不能の呼出し自体は解決済みのthread生成へ含めない。

通常のソース監査JSON、手書きのgraph、fixture、別revisionは総合gateを通過できない。
`full_extraction_verified`は上記の明示した解析範囲で全gateが合格した実OAI runだけtrueになる。
全OAIの解析完了を意味しない。12,244件ある固定版のcompile DB全体と、対象subsetを区別する。
静的候補・未知の別名・未解決呼出し・NULL callback・展開上限は成果物に残し、実行証拠を生成しない。
入力条件を変えた場合はcaptureからやり直す。ソース取得や依存準備だけの成功は受入完了を意味しない。

## ローカルでの再実行

Ubuntu 24.04の専用環境を使う。OS依存、LibToolingのビルド、固定ASN.1/SIMDeの導入と
shellへのexportは[実行マニュアル](runbook.md)の「固定OAIの総合受入を実行する」に記載する。
依存導入scriptはActionsでは`GITHUB_ENV`へ値を出すが、ローカルの親shellは変更しない。
Node.js 24/npm・Chromiumも導入した上で、まだ存在しない出力先から次の順に実行する。

```sh
python3 scripts/oai_ci.py --out out/oai-001 --tool build-libtooling/onion-extract --jobs 2 --capture-only
(
  ulimit -v 6291456
  python3 scripts/oai_ci.py --out out/oai-001 --resume-acceptance --require-browser
)
node scripts/check_oai_browser.cjs out/oai-001
```

capture成功だけでは未完了。`--require-browser`付きresumeは意味判定成功後もbrowser待ちで、
最後のChromium検証が成功して初めて`run.json.passed`がtrueになる。
分割optionなしの実行はブラウザなしの意味判定で成功し得るため、`passed`だけを総合受入の根拠にしない。
`semantic_acceptance_verified`・`browser_verified`とbrowser結果も確認する。
入力を変更した場合は新しい出力先でcaptureからやり直す。

OSパッケージとrunner imageの更新はmanifest・run.jsonに記録する。
OAI/ASN.1/SIMDeの版と実内容は固定するが、OSパッケージ一覧は再現に必要な記録であり
完全なコンテナimageやパッケージアーカイブの代替ではない。

## Artifactsと判定

| 成果物 | 確認事項 |
| --- | --- |
| `run.json`, `failures.json`, `*.log` | 実行ref、runner、固定条件、段階別成否、ビルドとエラー診断 |
| `workspace/preparation.json`, compile DB, CMakeCache, File API | 実configure/build条件とtargetの由来 |
| `dependencies.json`, `inputs/manifest.json` | ソース、tool、OS依存、外部依存、生成物の版・hash |
| `inputs/{targets,excluded,failures}.json` | 対象／除外理由／入力検証失敗の全一覧 |
| `extraction/` | 各TUのnative CFGと呼出し・抽出ログ・失敗一覧 |
| `acceptance/acceptance.json`, `oai-*.json` | 全体成否と固定経路・地点の個別gate結果 |
| `acceptance/<target>/{workspace,structure,view}.json` | 全関数を含むworkspace、構造、監査対象CFGの表示projectionとdisplay_scope |
| `acceptance/structure.html` | 自己完結の表示成果物。ダウンロード後offlineで開く |
| `browser/{summary.json,*.png}` | 実OAI HTMLをChromiumで操作した結果とthread/Queue/完了通知取得の5画面 |

失敗時も`!cancelled()`のArtifact uploadを実行する。取得・依存準備・buildの前で止まった場合、
後段成果物が無い理由はrun.jsonまたはActionsのstepログで確認する。表示成果物が作れた場合も
それだけでは総合成功と判断せず、`run.json`の`passed`・`semantic_acceptance_verified`・`browser_verified`、
`acceptance.json.full_extraction_verified`とbrowser結果を確認する。
成果物の保存期間は14日。M1の#11・#12とMilestoneはクローズ済みで、受入run・commit・成果物は
[M1受入結果](m1-results.md)に記録している。再実行時は新しいrunのURL・commit・成否を保存する。
