# M1/T9: CIと固定版OAIの総合受入

T9 (#11、親 #12) はT3〜T8を一巡させる。通常の必須CIは小さなfixtureだけを
使い、OAIの取得・USRP・RFドライバ・実softmodemの実行に依存しない。
`scripts/ci.py`はskipを失敗とし、既存のwrapper、callback/inline、Queueの別名・応答、
未解決・入力変更の回帰に加えて、両main → thread/Queue/callback → CFG → HTMLを
`onion.acceptance`で検証する。`out/acceptance`も通常のArtifactsに含む。
再構成・探索の複数run回帰は`test_model_native.py`で同じnative抽出から実行する。
fixtureの合格をOAIの合格とは扱わない。

## 固定OAI workflow

GitHub Actionsの **OAI pinned acceptance** (`.github/workflows/oai.yml`) を
`workflow_dispatch`で対象refを選んで実行する。workflowが既定ブランチへ統合された後に
Actionsの手動実行一覧から利用できる。workflow/実行driver自体を変更するPRでも
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
4. 固定`build_oai`引数（gNB、nrUE、SIMU、Clang 18、RelWithDebInfo、ccache無効）で
   configure/buildする。File API queryはconfigure前に置く。さらにCMake buildの終了値と
   両実行ファイルの存在・hashを確認する。build_oaiの成功メッセージだけでは合格させない。
5. T2のOAI profileで両ターゲットの依存閉包と全compile DBを照合し、
   `config/oai-analysis-scope.json`の10翻訳単位について入力検証とT3を実行する。
   固定source auditの全Cソースを含み、ヘッダは実際の依存として検証する。
   このsubset以外もmanifestから削除せず、未解析の除外理由を明記する。
   requested sourceが依存閉包に無い場合は失敗。同一sourceの複数条件は全て保持する。
   manifestのコンパイル条件を削ったり、生成ヘッダ不足を握りつぶしたりしない。
6. 各targetについてT4/T5/T6 → T1/T7 → T8のworkspace・構造モデル・表示projection・
   offline HTMLを出力する。T4の5経路、T5の13地点、T6のソース18地点中の意味地点と
   6候補接続を固定byte位置・hashから再計算する。contract文は別途source hashで確認する。

通常のソース監査JSON、手書きのgraph、fixture、別revisionは総合gateを通過できない。
`full_extraction_verified`は上記の明示した解析範囲で全gateが合格した実OAI runだけtrueになる。
全OAIの解析完了を意味しない。12,244件ある固定版のcompile DB全体と、対象subsetを区別する。
静的候補・未知の別名・未解決呼出し・NULL callback・展開上限は成果物に残し、実行証拠を生成しない。
入力条件を変えた場合はcaptureからやり直す。ソース取得や依存準備だけの成功は受入完了を意味しない。

## ローカルでの再実行

Ubuntu 24.04へworkflowと同じOS依存を用意してから、リポジトリのrootで実行する。
依存prefix・出力先はいずれも新しいパスを使う。

```sh
python scripts/install_oai_dependencies.py --prefix out/deps-001 --jobs 2
# environment.jsonの各値を現shellへexportする
cmake -S tools/libtooling -B build-libtooling -G Ninja \
  -DLLVM_DIR=/usr/lib/llvm-18/lib/cmake/llvm -DClang_DIR=/usr/lib/llvm-18/lib/cmake/clang
cmake --build build-libtooling --parallel 2
python scripts/oai_ci.py --out out/oai-001 --tool build-libtooling/onion-extract --jobs 2
```

依存導入scriptはGitHub Actionsでは`GITHUB_ENV`へ環境を出力する。
他の環境ではenvironment.jsonの各値をそのままexportする。
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
| `acceptance/<target>/{workspace,structure,view}.json` | 両main、thread、callback、Queue、未解決点、CFG参照 |
| `acceptance/structure.html` | 自己完結の表示成果物。ダウンロード後offlineで開く |

失敗時も`!cancelled()`のArtifact uploadを実行する。取得・依存準備・buildの前で止まった場合、
後段成果物が無い理由はrun.jsonまたはActionsのstepログで確認する。表示成果物が作れた場合も
それだけでは総合成功と判断せず、`run.json.passed`と`acceptance.json.full_extraction_verified`を確認する。
成果物の保存期間は14日。実行結果のURL・commit・成否は#11に記録し、レビュー・`dev/m1`への
マージ・受入条件確認後に#11を閉じる。M1の`main`への最終統合は#12で管理する。
