# OnionTrace

[![CI](https://github.com/fregata-ariel/OnionTrace/actions/workflows/ci.yml/badge.svg)](https://github.com/fregata-ariel/OnionTrace/actions/workflows/ci.yml)

Cプログラムの実行構造を静的に抽出し、mainからスレッド・Queue・callbackと関数内CFGをたどる解析ツールの試作です。
小規模pthread fixtureでは、少数のプローブで記録したイベントから候補経路を再構成し、次に測る地点を推薦します。
観測境界と過去の候補を残し、異なるrunの証拠を一回の実行履歴へ接合しません。

**M1は完了し、mainへ統合済みです。** 固定OAI `2026.w07` の両softmodemをSIMU条件で実ビルドし、
監査対象の構造抽出からCFG表示までを検証しています。OAIの構造は静的解析結果であり、OAI本体の実行記録ではありません。

## M1でできること

| 機能 | できること |
| --- | --- |
| 解析入力の固定 | compile DBとCMake File APIからtarget・コンパイル条件・依存hashを記録。同一ソースの異なる条件を保持し、対象・除外・失敗を分ける |
| ソース抽出 | Clang 18 LibToolingで関数内CFG、型、呼出し箇所、成立条件、マクロの展開／記述位置を抽出する |
| 実行構造の接続 | TU間の直接呼出し、ラッパー経由のスレッド生成、callback／inline、notifiedFIFOの投入・取得・処理・応答候補を接続する |
| 構造とCFGの閲覧 | 両mainから段階展開し、関連する関数内CFGとソース根拠・未解決理由をオフラインHTMLで確認する |
| 再構成と探索 | 明示したプローブ・区間・観測契約に対して候補経路を再構成し、追加地点を推薦する。親子区間、plan/run、過去候補の出典を保持する |
| 検証 | OAI不要のfixture CIと、固定OAIの実ビルド・構造抽出・表示を確認する独立workflowを実行する |

M1の固定OAI受入では、全12,244 compile DBエントリを保持し、**監査対象10翻訳単位**を解析しました。
thread 5経路・callback 13地点・Queue 6候補接続、全6受入判定・実Chromium 5操作を確認しています。
通常CIは200テスト（skip 0）・Chromium 16テスト・必須5ジョブが成功しています。
これは2026-10-19のM1完了時点の実績です。[条件・範囲・証拠](docs/m1-results.md)を参照してください。

## できないこと・M1のスコープ外

- **OAI本体の実行イベント取込や自動計測**：OAIへのプローブ自動挿入、T tracer等との接続、実job IDやpayloadの収集は未実装です。実イベントを使う段階的デモはpthread fixtureです。
- **静的構造からの実行確認・時間算出**：ソース上の接続を実行済みとは判定しません。OAIの処理時間、純粋なQueue滞留時間、実行時の所有権移動やworker対応は証明しません。
- **全OAIの完全な解析**：全翻訳単位・全経路・全callbackの解決、完全な関数ポインタ／別名解析は提供しません。未知・NULL・複数候補・探索打切りを保持します。
- **USRP・リアルタイム性能の検証**：RFドライバ、USRP/UHD/FPGAとの接続、リアルタイム動作や計測負荷の性能保証は対象外です。
- **ビルドをまたぐ自動的な同一視**：異なるbuild・target・コンパイル条件・runの証拠を自動で同じものとは扱いません。

M1内の制約もあります。主な検証対象はCで、C++の完全な意味解析は提供しません。
静的CFGの候補は経路の実行可能性の証明ではなく、`resolved`も実行確認ではありません。
固定OAIのHTMLは監査対象関数のCFGに絞り、全抽出関数の詳細はworkspace/native JSONに残しています。
全関数のCFGを一度に表示する機能ではありません。区間再構成には明示的なプローブ対応と観測契約が必要です。

## まずfixtureで動かす

Linux、Python 3.10以降、Clang/LLVM 18、CMake/Ninja、C/C++ビルド環境が必要です。
以下はUbuntu 24.04で、リポジトリのルートから実行する例です。

```bash
sudo apt-get update
sudo apt-get install -y python3 git build-essential clang-18 libclang-rt-18-dev \
  libclang-18-dev llvm-18-dev cmake ninja-build
python3 scripts/ci.py --cc clang-18 --out out
```

このコマンドは小規模fixtureをビルド・実行し、構造抽出・再構成・HTML生成と必須Pythonテストを行います。
**OAIの取得・ビルドやUSRPは不要です。** 再実行では`out`の固定名出力を置き換えるため、
結果を残すときは`--out out/experiment-001`のように出力先を分けてください。

| 開くHTML | 内容 |
| --- | --- |
| `out/onion-demo.html` | 実pthread fixtureの3段階の計測、job選択、候補経路、イベント再生、追加地点の推薦 |
| `out/structure-only.html` | 小規模の構造抽出用fixtureから、両main・thread・Queue・callbackとCFGを段階展開 |
| `out/structure-demo.html` | 同じ構造画面と、別buildのpthread fixture時間軸を切替。証拠は混ぜない |

HTMLはファイルとしてブラウザで開けます。閲覧にサーバやNode.jsは不要です。
初回取得、成果物だけの閲覧、画面操作、固定OAIの実行、失敗時の確認は **[実行マニュアル](docs/runbook.md)** にまとめています。

## テストとCI

fixtureの生成後、Node.js 24/npmを用意してブラウザも確認できます。

```bash
npm ci
npx playwright install --with-deps chromium
npm test
```

通常CIはmainへのpush・PR・手動実行で動作し、Python 3.10／3.12／3.14、ASan/UBSan、DOM/Chromiumの5ジョブを実行します。
skip、欠落、build/planの不一致、探索上限、runの混同などを検証します。
固定OAIは別の **OAI pinned acceptance** workflowで手動実行でき、通常のfixture CIから分離しています。
生成HTML・JSON・ログ・ブラウザ結果はActionsのArtifactsへ14日間保存します。
詳しい実行コマンドと独自出力先の指定は[実行マニュアル](docs/runbook.md)を参照してください。

## ドキュメントと構成

入口は[ドキュメント一覧](docs/README.md)です。

| 文書 | 読む目的 |
| --- | --- |
| [実行マニュアル](docs/runbook.md) | 導入・fixture・固定OAI・画面操作・成果物・トラブル対応 |
| [M1受入結果](docs/m1-results.md) | 完了時の検証条件・解析範囲・CI・成果物の証拠 |
| [固定OAI受入の仕様](docs/oai-acceptance.md) | 各判定の意味、分割実行、成功・失敗の読み方 |
| [JSON契約](CONTRACT.md)／[実行構造モデル](docs/execution-structure.md) | 型付き接続・識別子・証拠の境界 |
| [構造ビューア](docs/structure-viewer.md)／[再構成への接続](docs/model-integration.md) | 入力・選択保持・区間・不完全性・plan/runの扱い |

主な実装は`onion/`、Clang抽出器は`tools/libtooling/`、表示は`viewer/`、
fixtureは`fixture/`、検証と実行driverは`scripts/`と`tests/`にあります。
OAIのソース・ビルド済みbinary・生成CFGは同梱していません。
