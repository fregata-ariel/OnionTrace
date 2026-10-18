# M1/T6: notifiedFIFOの静的受渡し候補

親Issue #12、対象Issue #8。T4のmain／スレッド役割とT5のcallback登録に、Queueの初期化・投入・取得・処理・応答先のソース根拠を加える。T7（#9）がT1実行構造／探索器へ変換し、T8（#10）が画面へ接続する。

## 入出力

```bash
python3 -m onion.queues --manifest out/input/manifest.json \
  --extraction out/extraction/extraction.json --target nr-softmodem --out out/queues
```

T2の`build_input_manifest`、T3の`source_extraction_manifest`と、ハッシュ検証済みの実際のLibTooling成果物を読み、`queues.json`と`failures.json`を出力する。失敗・欠落TU、再帰／文脈展開上限は保持し、準備不完了ではCLIを非0終了する。`--max-contexts`（既定10000）、`--max-depth`（128）は正数。

LibToolingはT5の値領域を変えず、各関数に`queue_semantics_version: 1`、各呼出しに`queue_api`、`queue_arguments`、`queue_callee`を追加する。各writeには`queue_target`／`queue_value`を併記する。宣言・メンバー・address-of・dereference・cast・添字・呼出し結果・条件付き値を保持する。旧成果物は再抽出を要求する。

## 中間グラフの契約

`notified_fifo_graph` v1はT1の`execution_structure`やrun証拠とは別の文書。manifest ID、抽出digest、callback graph digest、API契約、target別graphを持つ。`ready`は選択TUの抽出と文脈展開が完了した意味で、全別名の解決や経路の成立を意味しない。

| フィールド | 内容 |
| --- | --- |
| `functions`, `call_relations`, `spawns` | T4由来の関数・CFG・呼出し・スレッド役割と未解決境界 |
| `operations` | initialize/create/enqueue/dequeue/poll/destroy/abortの地点・引数・条件・CFG block |
| `queue`, `queue_candidates` | 元式・転送／write根拠、基底宣言USRとscope、フィールドUSR列、添字、未解決理由 |
| `context` | main／スレッド役割、直接呼出し経路、条件。到達未確認の地点はroleをNULLにする |
| `processing_calls` | 取得要素のcallback地点、または取得dataを渡す直接処理呼出しと内部CFG参照 |
| `callback_registrations` | T5の関数候補・NULL・unknown・登録根拠。createからIDで参照 |
| `relations` | 下表のmay-linkと両端のソース根拠。すべて`resolution: candidate` |
| `frontiers`, `thread_frontiers`, `failures` | 上限・未解決呼出し・失敗した入力 |

| relation | 意味 |
| --- | --- |
| `initializes` | 互換な論理Queueの初期化候補 |
| `submits` | 投入引数が由来を持つ要素生成地点 |
| `transfers` | 投入／取得のQueue候補が互換。実行済みjob移動を表さない |
| `processes` | 取得要素のcallback、またはそのdata由来の直接処理呼出し |
| `response_destination` | 取得要素の`reponseFifo`（OAI原文の綴り）へ同じ取得由来の要素を投入 |
| `returns_to`, `response_transfer` | 元投入の生成時応答先・明示的な応答先書換えに基づく返却候補 |
| `completion_candidate` | 処理関数内の別の投入。新規要素の場合もあり、同じjobの返却とはしない |

同名フィールドでもUSRが違えば一致させない。`&first.fifo`と`&second.fifo`は基底宣言が異なるので結ばない。未変更引数は実引数を展開し、threadCreateの引数にも同じ根拠を保持する。基底が未解決のポインタでは、同じフィールドUSRを持つ**論理的な系列**を候補にできるが、同じオブジェクトとは断定しない。`info->gNB->fifo`等のポインタフィールドをまたぐと新しい基底個体は未解決になり、元の格納場所を`via`に保持する。完全に不明な戻り値／別名は候補なし・理由付きで残す。

ポインタやポインタフィールドのwriteはflow-insensitiveな代替候補であり、最終代入の証明を行わない。既知の書換え先と元値を残し、未知の上書き・escapeも残す。`&p`をpの指すQueueへ縮約しない。明示cast、未知添字、展開上限も不確実性として残す。到達不能／意味不完全な呼出しはblockedとし、応答先を介する間接の接続にも利用しない。完全なheap／points-to、関数戻り値・payload内ポインタの全域追跡は対象外。

## 所有権と将来の計測候補

APIはOAIの名前・record tag・引数／戻り型の互換性で認識する。実装そのものの同一性は固定ソースの監査で確認する。契約は`api_contracts`と各操作の`semantics`へ保存する。fixtureのstubをこの並行実装と同一視しない。

| 操作 | 境界と安全に参照できる情報 |
| --- | --- |
| create | 生成者が所有。応答先指定はmetadataであり、まだ移動ではない |
| enqueue | 関数入口は試行。非abortをlock内で確認して公開し、unlockで取得側へ見える。公開前に必要なkey等をコピーし、unlock後は要素を参照しない |
| dequeue | lock内の成功した取出しが所有権取得。非NULL戻り値を確認した後は要素を安全に読める。NULLはabort |
| poll | 非NULLの時だけ取得。NULLには競合・空・abortが含まれる |
| response enqueue | 非NULL応答先やcallbackの条件を保持。再公開後の要素参照を避ける |
| destroy / abort | 要素を解放し得る。以後の参照や、abort時の移動成功を仮定しない |

これらは計測候補の意味情報であり、プローブを挿入しない。`runtime_instance`は常にNULL。実アドレス、job ID伝播、payload収集、実行済み移動、動的worker対応、時刻を生成しない。push入口とpull戻りの差にはlock待ち・処理・観測位置のずれが含まれ得るため、純粋なQueue滞留時間とは解釈しない。

## 固定OAIで確認した経路

OAI `2026.w07`、commit `7fa4d018d4178f9664ea1ca377eca2e822c7a979`。`config/oai-queue-sites.json`は4ソースのSHA-256と18地点のbyte範囲・行を固定する。今回のsource auditは成功。実際のOAIビルド／全体抽出受入は#11で行う。

| ソース | 地点と関係 |
| --- | --- |
| `executables/nr-gnb.c:349,352` | resp_L1とL1_rx_outの初期化 |
| `nr-gnb.c:110,117` | tx_funcでNULL callbackの要素を生成し、resp_L1へ投入 |
| `nr-gnb.c:149,154,156` | L1_rx_threadで取得、rx_funcを直接呼出し、元要素を破棄 |
| `nr-gnb.c:221,228` | rx_func内のUL／MIXED条件で別の完了要素を生成し、L1_rx_outへ投入 |
| `executables/nr-ru.c:960,1145` | ru_threadからQueueを引数で渡し、wait_free_rx_ttiで完了通知を取得 |
| `common/utils/actor/actor.c:47,54,56` | 取得・callback・reponseFifoへの返却。gNBの直接処理と区別 |
| `common/utils/threadPool/notified_fifo.h:137,140,154,171` | 公開／unlock、lock内取出し、戻りの境界 |

このgNB経路ではresp_L1の要素がそのままL1_rx_outへ移るわけではない。各地点のgNBポインタが同一の実行時個体かは、ソースのfield／引数候補だけでは確定しない。

```bash
# ソースだけの監査。full_extraction_verifiedは常にfalse
python3 -m onion.oai_queues --source-root /path/to/pinned-oai --out out/queue-source-audit.json
# #11用: 実manifestから再計算し、地点・hash・6つの候補接続を検証
python3 -m onion.oai_queues --manifest out/oai-input/manifest.json \
  --extraction out/oai-extraction/extraction.json --out out/queue-acceptance.json
```

総合gateは両実行targetを要求し、nr-softmodem側の固定地点、NULL callback、ソースhash、処理と完了投入までの接続を再計算する。source auditや手書きの構造図だけでは通過しない。通常CIはOAI取得不要で、`fixture/queues`の実ネイティブ抽出を必須テストに含め、`out/queues`へmanifest・抽出・候補グラフ・失敗情報を保存する。
