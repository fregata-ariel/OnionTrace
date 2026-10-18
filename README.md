# OnionTrace

[![CI](https://github.com/fregata-ariel/OnionTrace/actions/workflows/ci.yml/badge.svg)](https://github.com/fregata-ariel/OnionTrace/actions/workflows/ci.yml)

CFGと少数の実行時プローブで、未確定な処理区間を段階的に詳しくする解析ツールの試作です。
前段階の観測境界を残し、次の計測点を選び、実行証跡を同じ構造に重ねます。
異なるrunの証拠は出典を残して蓄積し、一回の履歴に接合しません。

現在はpthreadとFIFOを使う小さなCプログラムで検証しています。OAIやUSRPのビルド・実行環境は不要です。

## ローカルで実行する

Linux、Python 3.10以降、Clang 18が必要です。Ubuntu 24.04では次で準備できます。

```bash
sudo apt-get update
sudo apt-get install -y clang-18 libclang-18-dev llvm-18-dev cmake ninja-build
python3 scripts/ci.py --cc clang-18 --out out
```

このコマンドはCのビルド、Clang CFG抽出、3段階の実行、自動プローブ選定、必須Pythonテストを実行します。
毎回データを再生成し、skipが一件でもある場合は失敗します。`out`内の固定名の出力は置き換えます。
実験を残す場合は、`--out runs/experiment-001`のように別ディレクトリを指定してください。

`out/onion-demo.html`をブラウザで開くと、段階切替、job選択、CFG、イベント再生、推薦理由を確認できます。
HTMLの閲覧にはサーバやNode.jsは不要です。生成したplan、trace、analysisは`out/stage-N`に保存します。

## テスト

```bash
# 結合テストは必要な実行データを自身で生成します。
python3 -m unittest discover -s tests -v

# CMake経由のビルドとCTest
cmake -S fixture -B build -G Ninja -DCMAKE_C_COMPILER=clang-18
cmake --build build
ctest --test-dir build --output-on-failure

# Node.js 24でDOM・実ブラウザを確認
npm ci
npx playwright install --with-deps chromium
npm test
```

`npm test`の前には`python3 scripts/ci.py`を実行してください。通常の表示データと、
イベント欠落時の推薦を確認する`out/incomplete-demo.html`を生成します。
独自の出力先を使った場合は、Playwrightへ`ONION_OUTPUT=/absolute/output`を渡せます。
DOMチェックは`node tests/check_dom.cjs /absolute/output/onion-demo.html /absolute/output/qa-dom`です。

Python単体テストだけなら`python3 -m unittest discover -s tests -p test_core.py -v`で実行できます。
実行系のテストは`ONION_CLANG`でコンパイラを選べます。

## CIで確認すること

push（main）、pull request、手動実行でGitHub Actionsが動作します。

| ジョブ | 確認内容 |
| --- | --- |
| Core / Python 3.10, 3.12, 3.14 | Clang 18、CMake/CTest、CFG抽出、3段階の実行、再構成・探索・実行系の必須テスト |
| C fixture / ASan + UBSan | Queueの周回、最大job数、全プローブ有効時のメモリ・未定義動作検査 |
| Viewer / Chromium | DOM/SVG、段階切替、job選択、再生、欠落時の推薦、オフライン動作、モバイル幅 |

欠落、thread内の順序不整合、build/planの取り違え、循環や探索上限、run間の混同を検査します。
プローブの有効・無効で計算結果が変わらないことと、バッファが小さくても欠落を報告して処理が完了することも確認します。

生成HTML、CFG、各段階のJSON、テストログ、ブラウザのレポート・画像はActionsのArtifactsに14日間保存します。
失敗時にも利用でき、CIで生成した実動作を手元で確認できます。これらは機能テストで、リアルタイム性能の保証ではありません。

## 構成

| パス | 責務 |
| --- | --- |
| `onion/cfg.py` | 比較用のClangテキストCFG取込 |
| `tools/libtooling/`, `onion/libtooling.py` | Clang 18の構造化CFG・呼出し抽出、manifest検証と既存Coreへの変換 |
| `onion/core.py` | 候補経路の再構成、追加地点の推薦、run別根拠の蓄積 |
| `onion/execution.py` | M1の実行構造・識別子・run証拠のJSON境界と参照検証 |
| `onion/build_inputs.py` | 固定版OAIの解析入力・CMakeターゲット・コンパイル条件のmanifest |
| `onion/demo.py` | ビルドから実行・HTML出力までの接続 |
| `fixture/` | 実スレッド・Queueと明示的な候補プローブ |
| `viewer/` | 外部依存のないHTML/SVG表示 |
| `scripts/ci.py` | データを再生成し、skipを許さず必須テストを実行 |
| `tests/` | 必須Pythonテスト、DOM確認、Playwrightテスト |
| `tests_optional/` | 外部提供のOAIデータを検証する任意テスト |

設計と制約は[docs/prototype-v0.1.md](docs/prototype-v0.1.md)、JSON境界は[CONTRACT.md](CONTRACT.md)に記載しています。

M1の[実行構造契約](docs/execution-structure.md)では、関数・スレッド役割・論理Queue・callbackと型付き接続を定義しています。
現在は手書きfixtureによる境界検証の段階です。OAIからの構造抽出と既存探索器への接続は後続のIssueで実装します。

OAIのCFGは別途取得した場合だけ`python3 -m onion.demo --oai-dump /path/to/cfg --out out`で表示できます。
外部コーパスの検査は`ONION_OAI_CFG_DIR=/path/to/cfg python3 -m unittest discover -s tests_optional -v`です。
OAIのソースやCFGダンプは同梱していません。

M1/T2の[解析入力の準備とmanifest](docs/oai-build-inputs.md)では、固定版OAIを独立したcheckoutに
取得し、SIMU・Clang 18の入力条件、生成ヘッダ、依存、対象・除外・失敗の由来を記録します。
小規模CMake fixtureは通常CIに含みます。OAI本体の総合検証は後続Issue #11で扱います。


M1/T3の[LibTooling抽出器](docs/libtooling-extractor.md)は、T2のmanifestからコンパイル条件ごとに
CFG・型・呼出し箇所・マクロ位置を構造化します。未解決呼出しと失敗を保持し、小規模fixtureで
既存の再構成・推薦との対応を検証します。通常デモのテキスト取込は比較用に継続します。

M1/T4のmainからスレッド開始関数への静的経路抽出は[thread-entrypoints](docs/thread-entrypoints.md)を参照。

M1/T5のActor・task callback登録／投入／実行候補とinline条件・スレッド役割は
[callback-contexts](docs/callback-contexts.md)を参照。候補・NULL・未解決とQueue別名の不確実性を保持します。

M1/T6のnotifiedFIFOの投入・取得・応答先、論理Queueの基底オブジェクト／引数と
公開・取得・所有権の境界は[notified-fifo](docs/notified-fifo.md)を参照。
固定gNB経路のソース根拠と、#11用の実抽出検証gateを含みます。
