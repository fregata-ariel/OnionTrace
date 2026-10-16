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
sudo apt-get install -y clang-18 cmake ninja-build
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
| `onion/cfg.py` | ClangテキストCFGの構造化。将来LibToolingで置換する境界 |
| `onion/core.py` | 候補経路の再構成、追加地点の推薦、run別根拠の蓄積 |
| `onion/demo.py` | ビルドから実行・HTML出力までの接続 |
| `fixture/` | 実スレッド・Queueと明示的な候補プローブ |
| `viewer/` | 外部依存のないHTML/SVG表示 |
| `scripts/ci.py` | データを再生成し、skipを許さず必須テストを実行 |
| `tests/` | 必須Pythonテスト、DOM確認、Playwrightテスト |
| `tests_optional/` | 外部提供のOAIデータを検証する任意テスト |

設計と制約は[docs/prototype-v0.1.md](docs/prototype-v0.1.md)、JSON境界は[CONTRACT.md](CONTRACT.md)に記載しています。

OAIのCFGは別途取得した場合だけ`python3 -m onion.demo --oai-dump /path/to/cfg --out out`で表示できます。
外部コーパスの検査は`ONION_OAI_CFG_DIR=/path/to/cfg python3 -m unittest discover -s tests_optional -v`です。
OAIのソースやCFGダンプは同梱していません。
