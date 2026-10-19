# M1受入結果と検証範囲

2026-10-19にM1は完了しました。T1〜T9（Issue #3〜#11）を受け入れ、
[統合PR #23](https://github.com/fregata-ariel/OnionTrace/pull/23)をmainへマージし、
[親Issue #12](https://github.com/fregata-ariel/OnionTrace/issues/12)と
[M1 Milestone](https://github.com/fregata-ariel/OnionTrace/milestone/2)をクローズ済みです。
本ページの数値は完了時点の実績であり、今後の実行で必ず同じ件数になるという指定ではありません。

## 固定条件

| 項目 | 受入時の条件 |
| --- | --- |
| OAI | `duranta-project/openairinterface5g`、tag `2026.w07`、commit `7fa4d018d4178f9664ea1ca377eca2e822c7a979` |
| target | `nr-softmodem`、`nr-uesoftmodem`。SIMUで両binaryを実ビルド |
| toolchain | Ubuntu 24.04、Python 3.12、Clang/LLVM 18、Ninja、RelWithDebInfo、ccache無効 |
| CPU | `--noavx512`、C/C++とも最終条件 `-march=x86-64-v3 -mgfni -mvpclmulqdq` |
| 外部依存 | ASN.1/SIMDeのcommitと専用prefixを固定。版・実ファイルhash・OSパッケージ一覧を記録 |
| 解析範囲 | [明示した10翻訳単位](../config/oai-analysis-scope.json)。その他のcompile DBエントリは除外理由を保持 |

取得・ビルド引数と依存commitの正本は[lock設定](../config/oai-2026.w07.json)です。
ホストCPU依存のClang 18ビルド失敗を受け、[PR #24](https://github.com/fregata-ariel/OnionTrace/pull/24)で
最終CPU条件を固定しました。OAIソースは変更せず、元引数とClangの最終CPU/feature展開を記録します。

## 確認した結果

| 対象 | 結果 |
| --- | --- |
| compile DB | 全12,244エントリをCMakeと照合。対象10／除外12,234／失敗0 |
| スレッド | 両mainからの固定5経路を、開始関数・call path・成立条件・位置・依存hashと照合 |
| callback | Actor／worker、inline、NULL、取得・steal等の13地点を検証 |
| Queue | 4ファイル18地点のソース照合と、14意味地点・6候補接続の実抽出判定を検証 |
| 受入判定 | 両targetのworkspace、viewer、thread、callback、Queueの全6判定が成功 |
| 実Chromium | 両mainからthread／QueueのCFG、完了通知の取得側へ進む5操作が成功。外部通信・ブラウザエラー0 |
| 通常CI | Python 200テスト（skip 0）、DOM検証、Chromium 16テスト、必須5ジョブ成功 |

Queueの18地点には公開／取得のcontract記述も含みます。すべてが独立したAPI呼出しではなく、
候補接続6件も実行時の同一Queue個体や受渡しを証明するものではありません。
検証したCFG表示先は、gNBの`L1_rx_thread`・`rx_func`・`wait_free_rx_tti`、
UEの`UE_thread`・`actor_thread`です。これとは別に、スレッドの固定5経路とcallbackの各地点を意味解析で判定しています。

HTMLのCFG本文はgNB 36/660関数、UE 30/548関数に限定し、`display_scope`を明記しました。
ここで分母は監査対象TUから抽出した関数数です。全OAIの関数数ではありません。
全抽出関数のCFGはworkspace/native JSONに保持し、未解決呼出しやfrontierも残しています。

## 証拠と再実行

| 証拠 | 確認先 |
| --- | --- |
| mainのマージ後CI | [run 39191991170](https://github.com/fregata-ariel/OnionTrace/actions/runs/39191991170) |
| 統合PRの通常CI | [run 39189339566](https://github.com/fregata-ariel/OnionTrace/actions/runs/39189339566) |
| 固定OAIの総合検証 | [run 39189339559](https://github.com/fregata-ariel/OnionTrace/actions/runs/39189339559) |
| 全成果物 | [artifact 12159304426](https://github.com/fregata-ariel/OnionTrace/actions/runs/39189339559/artifacts/12159304426) |
| native成果物 | [artifact 12159482829](https://github.com/fregata-ariel/OnionTrace/actions/runs/39189339559/artifacts/12159482829) |

mainマージcommitは`b09c7d2443d788c7dfd2c8421f3bccd2e6ad4d32`。
検証済みのtree `5493b3d7016adfa9a4e70706d02897a5eeedc1f2`と一致することを確認しました。
全成果物ZIPの記録digestは`sha256:a33d9e5e0e9af6392d9a5c882975a02e532a2002943efdc653add9f3ae90be7c`、
ダウンロードして照合したnative ZIPのdigestは`sha256:8108b0893dc57e588cc1b22b4e6dc52ca5e99e2fad2302bdbb2abf04bd7e3339`です。

Artifactsの保存期間は14日です。期限後は同じ成果物の取得を前提にせず、
[実行マニュアル](runbook.md)から新しい実行を行い、そのrunの条件・判定・成果物を確認してください。

M1が受け入れたのは指定範囲の静的構造と表示です。全OAIの呼出し解決完了、OAIの実行イベント収集、
Queue時間の測定、USRP接続、リアルタイム性能保証は含みません。
