# M1/T4: mainからスレッド開始関数への静的経路

Issue #6、親 #12。T2の`build_input_manifest`とT3の`source_extraction_manifest`から、
ターゲットごとにmainを根とする`thread_entrypoint_graph` v1を生成する。
これはIssue #9へ渡す中間データであり、T1の`execution_structure`や実行イベントではない。

## 実行

先に[入力準備](oai-build-inputs.md)と[LibTooling抽出](libtooling-extractor.md)を行う。
T4は引数の意味と条件を必要とするため、以前の抽出ファイルは更新した抽出器で再生成する。

```sh
python -m onion.threads \
  --manifest out/build-inputs/manifest.json \
  --extraction out/source-extraction/extraction.json \
  --target nr-softmodem --target nr-uesoftmodem --out out/threads
```

`threads.json`と`failures.json`を保存する。main不在・曖昧、TU欠落・抽出失敗、展開上限では
終了値1になる。解決できない通常呼出し・開始関数は有効な未解決データとして保持する。
`ready`は入力の抽出とmain・有界展開の準備状態であり、全接続の解決や実行成功を意味しない。

`--max-contexts`（既定10000）と`--max-depth`（既定128）で展開量を設定する。
再帰と上限到達は`frontiers`に残り、`traversal_complete: false`となる。
途中のTUに失敗があっても他の解析結果を失わず、`failures`と`extraction_complete: false`を残す。

## 接続ルール

- T2の`selected_by`により、実行ターゲットの依存閉包ごとに索引を分離する。
  共有ライブラリのコンパイルを両ターゲットで利用できるが、別実行ファイルの定義は混入させない。
- 同一TUの直接呼出しはT3の参照を使う。外部リンケージのTU間接続はUSR・リンケージ・
  正規化した関数型が一致し、定義が一つの場合だけ解決する。
  内部リンケージは同じコンパイルID内に限定する。複数定義・コンパイル条件は候補を保持し、選ばない。
- CFGのentryから到達しない呼出し、配置のないAST呼出し、意味の不完全性がある関数からは
  確定した経路を延長しない。未解決の途中関数を省略して開始関数へつながない。
- 関数designatorと`&function`、変更されていない仮引数の直接受渡しを扱う。
  代入・増減・アドレス取得のある仮引数は保守的に不明とする。
  変数の別名、明示cast、構造体field、戻り値、間接呼出しのpoints-to解析は行わない。
  三項式の関数候補は残すが、一つの確定開始関数にはしない。
- wrapperの実引数は呼出し文脈ごとに置換し、元の式・位置と`forwarded_at`を保持する。
  同じ開始関数、同じwrapper内の生成地点でも、mainからの呼出し地点が違えば別の役割IDになる。
  ループの動的な生成回数、実thread ID、スケジュールは推論しない。

## 生成API

既知の外部Cシンボルとprototypeの意味を契約として利用する。名前・USR、引数数、開始関数型
`void *(*)(void *)`、handle/引数のpointer型、戻り値をnative側で検査する。
同名・同型の別実装の挙動まで保証するものではない。

| API | handle | 開始関数 | 渡す引数 | 役割名 |
| --- | --- | --- | --- | --- |
| `pthread_create` | 第1引数 | 第3引数 | 第4引数 | 生成地点のID |
| OAI `threadCreate` | 第1引数 | 第2引数 | 第3引数 | 第4引数の式 |

`threadCreate`のaffinityとpriorityを含む全実引数はT3のcall recordに保持する。
契約に合うAPI呼出しを意味上の生成地点とし、その内部の`pthread_create`を重複計上しない。
既知APIの実装本体が抽出対象外でも、生成APIの意味と開始関数の定義を別々に扱える。

## 出力

各`graphs[]`は`target`、`root_id`、生のfunction/CFG/call、`relations`、`spawns`、
`frontiers`、失敗と完全性を含む。コンパイルID、入力manifest ID、抽出manifest digestを保持する。

| record | 内容 |
| --- | --- |
| `calls` relation | 関数→関数。解決先、候補、不明理由、call ID、ソース位置、条件 |
| `spawns` relation | 生成元関数→生成地点と呼出し文脈に対応するthread role |
| `starts` relation | thread role→開始関数。不明ならnullと候補・理由 |
| `spawns[]` | API、生成call、mainからの連続した`call_path`、handle・開始式・引数・役割名、条件 |
| `frontiers[]` | mainから到達した未解決呼出し、再帰・上限、CFG配置不明の境界 |

条件は、各callを囲むifのthen/else、for/while/do、三項式、短絡評価のRHS、switchの
ソース文脈を保持する。条件の元式・範囲とcall IDを残し、経路上の条件を生成地点へ累積する。
doの初回は無条件であることを明示する。プリプロセッサ条件は実際に選択された
コンパイル引数とコンパイルIDに属する。これらは**必要なソース文脈であり、実行可能性の証明ではない**。
先行return/goto、条件間の相関、switch fallthroughなどの全経路条件を論理式へ還元しない。

## 固定版OAIの根拠と総合受入

`config/oai-thread-paths.json`には、固定commit
`7fa4d018d4178f9664ea1ca377eca2e822c7a979`のソースを照合した5経路を記録する。
ファイルのSHA-256・git blob、callのbyte範囲・行番号・式・リンクを持つ。

| target | ソース上の経路 | 生成地点 |
| --- | --- | --- |
| nr-softmodem | main → init_eNB_afterRU → init_gNB_Tpool → L1_rx_thread | nr-softmodem.c:712 → nr-gnb.c:404 → nr-gnb.c:355 |
| nr-softmodem | 同上 → L1_tx_thread | nr-gnb.c:357 |
| nr-softmodem | 同上 → nrL1_stats_thread | nr-gnb.c:360（stats条件あり） |
| nr-uesoftmodem | main → init_NR_UE_threads → UE_thread | nr-uesoftmodem.c:457 → nr-ue.c:1094 |
| nr-uesoftmodem | 同上 → nrL1_UE_stats_thread | nr-ue.c:1097（stats条件あり） |

OAIの`common/utils/system.h:49`で契約を、`system.c:252–283`でpthreadへの受渡しを確認する。
gNB側のmainには別の関数からの同名呼出しもあるため、mainの712行目を明示している。

```sh
# ソース照合だけ。ビルド・抽出成功を意味しない。
python -m onion.oai_threads --source-root /absolute/oai/source \
  --out out/threads/oai-source-audit.json

# Issue #11の実ビルド・抽出後に実行する総合受入ゲート。
python -m onion.oai_threads --manifest out/build-inputs/manifest.json \
  --threads out/threads/threads.json --out out/threads/oai-acceptance.json
```

前者は`source_audit_passed: true`と`full_extraction_verified: false`を出す。
後者はOAI profile・固定revision・全TUの抽出成功を要求し、5経路すべての連続したcallと開始関数、
実ファイルパス、byte位置、manifestの依存ファイルhashを照合する。不一致・対象経路の途中未解決は失敗する。
対象5経路の外の再帰・未解決点は消さず、受入結果にも`traversal_complete`を併記する。
本PRで実施したのは固定ソース照合と通常CIのnative fixture。**固定版OAI全体の実ビルドを用いた
5経路の抽出と表示を含む総合検証は、親Issueの分担どおり#11で実行する。**

## 通常CI

`fixture/threads`は直接生成、二段wrapper、分岐・ループ、同じ開始関数を使う別地点、
異なるtargetの同名static関数、未知pointer・三項式候補・変更された仮引数、未定義中間関数、
重複コンパイル定義を持つ。故意に未解決のlink inputを含むためconfigure・構文検査・抽出だけを行い、
実行・リンクテストには使わない。通常の実行fixtureは従来どおり別途ビルド・実行する。

`tests/test_threads.py`はmissing toolをskipせず、上記に加えてTU欠落・失敗、改変artifact、
展開上限、CLI終了値を検証する。`scripts/ci.py`は両mainのfixture抽出結果とmanifestを
`out/threads/`へ保存し、既存のActions artifactに含める。
