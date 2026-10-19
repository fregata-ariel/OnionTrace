# ドキュメント一覧

M1はmainへの統合・最終受入を完了しています。初めて使う場合は[実行マニュアル](runbook.md)、
できることと対象外の確認には[README](../README.md)を参照してください。

## 利用と検証

| 文書 | 内容 |
| --- | --- |
| [実行マニュアル](runbook.md) | 環境準備、fixture、固定OAI、HTML操作、成果物、失敗時の確認 |
| [M1受入結果](m1-results.md) | 固定revision・解析範囲、完了時の実績、CIと成果物へのリンク |
| [固定OAI総合受入](oai-acceptance.md) | workflow、各判定、capture／resume／browserの成功条件 |
| [構造ビューア](structure-viewer.md) | workspaceからのHTML生成、選択と証拠、表示範囲と上限 |
| [pthreadデモの設計](prototype-v0.1.md) | 3段階の計測・推薦・再構成。OAIの実行証跡ではない |

## 入力・抽出・モデルの仕様

| 文書 | 対象 |
| --- | --- |
| [JSON契約](../CONTRACT.md) | Coreと実行構造の入出力境界 |
| [実行構造と識別子](execution-structure.md) | T1：entity、型付き接続、run証拠と累積 |
| [OAI解析入力](oai-build-inputs.md) | T2：固定条件、compile DB／File API、依存hash、除外・失敗 |
| [LibTooling抽出器](libtooling-extractor.md) | T3：CFG、型、呼出し、位置、未対応構文 |
| [スレッド開始関数](thread-entrypoints.md) | T4：mainから生成wrapper・開始関数への静的経路 |
| [callbackとinline](callback-contexts.md) | T5：Actor／workerの登録・投入・実行候補 |
| [notifiedFIFO](notified-fifo.md) | T6：投入・取得・処理・応答、別名と所有権の境界 |
| [再構成・探索との接続](model-integration.md) | T7：関数内区間、親子境界、plan/run、不完全性 |

T1〜T9はM1内の実装分担を示す名称です。文書中のsource auditはソース位置・hashの照合、
native extractionは実コンパイル条件での抽出、runtime traceは実行イベントを指します。
固定OAIの受入は前二者とブラウザ操作を検証し、OAI本体のruntime traceは生成していません。
